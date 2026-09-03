import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from n0te2.consumer_shell import ConsumerShell
from n0te2.instance import ProcessIdentity
from n0te2.memory import HeadquartersMemory
from n0te2.platforms import PlatformEnvironment


class Probe:
    def status(self, process: ProcessIdentity) -> str:
        return "UNKNOWN"


def process(pid: int, token: str) -> ProcessIdentity:
    return ProcessIdentity.from_start_token(
        PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
        pid=pid,
        start_token=token,
    )


def new_shell(data_root: Path, state_root: Path, pid: int, token: str) -> ConsumerShell:
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(pid, token),
        probe=Probe(),
    )
    shell.start()
    return shell


def get(shell: ConsumerShell, path: str) -> tuple[int, str]:
    req = Request(shell.address.origin + path, method="GET")
    try:
        with build_opener().open(req, timeout=3.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def post(
    shell: ConsumerShell,
    path: str,
    fields: dict[str, str],
    *,
    origin: str | None = None,
) -> tuple[int, str]:
    payload = urlencode(fields).encode("utf-8")
    req = Request(shell.address.origin + path, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if origin is not None:
        req.add_header("Origin", origin)
    try:
        with build_opener().open(req, timeout=3.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def action_for(page: str, path: str) -> str:
    match = re.search(
        rf'<form[^>]+action="{re.escape(path)}".*?name="action" value="([^"]+)"',
        page,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def selection_action_for_name(page: str, name: str) -> str:
    match = re.search(
        r'<li class="stack">(?:(?!</li>).)*?<strong>'
        + re.escape(name)
        + r'</strong>(?:(?!</li>).)*?<form method="post" action="/template/select">'
        + r'.*?name="action" value="([^"]+)"',
        page,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def seed_song(data_root: Path) -> None:
    hq = HeadquartersMemory.create(data_root, "Template Consumer")
    try:
        hq.store.create_song("Reusable Start Song")
    finally:
        hq.close()


def save_fields(shell: ConsumerShell, page: str, *, name: str, capability: str) -> dict[str, str]:
    return {
        "csrf": shell._csrf,
        "action": action_for(page, "/template/save"),
        "template_name": name,
        "family": "VOCAL",
        "intent": "Keep this starting intent reusable above any specific DAW",
        "capability": capability,
        "role_description": "Prepare the lead vocal while preserving performance intent",
    }


def test_song_page_saves_selects_and_relaunches_provider_neutral_template(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_song(data_root)

    shell = new_shell(data_root, state_root, 13101, "template-save")
    try:
        status, page = get(shell, "/song")
        assert status == 200
        assert page.count("<h2>Templates</h2>") == 1
        assert "No saved Templates yet" in page
        assert "Application boundary" in page
        assert "Real application readiness requires observed Studio capability facts" in page

        status, result = post(
            shell,
            "/template/save",
            save_fields(shell, page, name="Vocal Start", capability="vocal.tighten"),
            origin=shell.address.origin,
        )
        assert status == 200
        assert "Vocal Start" in result
        assert "Keep this starting intent reusable" in result
        assert "vocal.tighten" in result
        assert "Prepare the lead vocal" in result
        assert "Selected for this Song" in result
        assert "Nothing was applied to a DAW" in result
        assert "Apply Template" not in result
        assert "template:" not in result
        assert "tsel_" not in result
    finally:
        shell.stop()

    relaunched = new_shell(data_root, state_root, 13102, "template-relaunch")
    try:
        status, page = get(relaunched, "/song")
        assert status == 200
        assert "Vocal Start" in page
        assert "Selected for this Song" in page
        assert "vocal.tighten" in page
    finally:
        relaunched.stop()


def test_artist_can_switch_between_saved_templates_without_applying_host_state(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_song(data_root)
    shell = new_shell(data_root, state_root, 13103, "template-switch")
    try:
        _, page = get(shell, "/song")
        _, first = post(
            shell,
            "/template/save",
            save_fields(shell, page, name="First Start", capability="vocal.tighten"),
            origin=shell.address.origin,
        )
        _, second = post(
            shell,
            "/template/save",
            save_fields(shell, first, name="Second Start", capability="vocal.harmony.build"),
            origin=shell.address.origin,
        )
        assert "Second Start" in second
        assert "Selected for this Song" in second
        token = selection_action_for_name(second, "First Start")
        status, switched = post(
            shell,
            "/template/select",
            {"csrf": shell._csrf, "action": token},
            origin=shell.address.origin,
        )
        assert status == 200
        first_block = re.search(
            r'<strong>First Start</strong>.*?<p class="status good">Selected for this Song</p>',
            switched,
            flags=re.DOTALL,
        )
        assert first_block is not None
        assert "Nothing was applied to a DAW" in switched
        count = int(
            shell.runtime.headquarters.store._conn.execute(
                "SELECT COUNT(*) FROM operations"
            ).fetchone()[0]
        )
        assert count == 0
    finally:
        shell.stop()


def test_foreign_origin_is_rejected_before_consuming_template_action_and_replay_fails(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_song(data_root)
    shell = new_shell(data_root, state_root, 13104, "template-auth")
    try:
        _, page = get(shell, "/song")
        fields = save_fields(shell, page, name="Protected Start", capability="audio.repair")
        status, _ = post(
            shell,
            "/template/save",
            fields,
            origin="https://example.invalid",
        )
        assert status == 403

        status, saved = post(
            shell,
            "/template/save",
            fields,
            origin=shell.address.origin,
        )
        assert status == 200
        assert "Protected Start" in saved

        status, replay = post(
            shell,
            "/template/save",
            fields,
            origin=shell.address.origin,
        )
        assert status == 409
        assert "already handled or expired" in replay
    finally:
        shell.stop()


def test_save_action_is_bound_to_the_song_that_rendered_it(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_song(data_root)
    shell = new_shell(data_root, state_root, 13105, "template-song-binding")
    try:
        _, page = get(shell, "/song")
        stale_fields = save_fields(
            shell,
            page,
            name="Stale Start",
            capability="vocal.tighten",
        )
        start_action = shell._new_action("song-start")
        status, changed = post(
            shell,
            "/song/start",
            {
                "csrf": shell._csrf,
                "action": start_action,
                "song_title": "Different Song",
            },
            origin=shell.address.origin,
        )
        assert status == 200
        assert "Different Song" in changed

        status, blocked = post(
            shell,
            "/template/save",
            stale_fields,
            origin=shell.address.origin,
        )
        assert status == 409
        assert "active Song changed" in blocked
        assert "Stale Start" not in blocked
    finally:
        shell.stop()
