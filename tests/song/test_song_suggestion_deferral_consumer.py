from __future__ import annotations

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


def post(shell: ConsumerShell, path: str, fields: dict[str, str], *, origin: str | None = None) -> tuple[int, str]:
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
    assert match is not None, path
    return match.group(1)


def seed_song(data_root: Path) -> None:
    hq = HeadquartersMemory.create(data_root, "Not Now Consumer")
    try:
        song = hq.store.create_song("Signal Bloom")
        hq.sessions.start_session(song_id=song.id, objective="Strengthen the chorus lift")
    finally:
        hq.close()


def prepare(shell: ConsumerShell, *, distance: str = "ADJACENT") -> tuple[str, str]:
    _, page = get(shell, "/song")
    status, result = post(
        shell,
        "/suggestion/create",
        {
            "csrf": shell._csrf,
            "action": action_for(page, "/suggestion/create"),
            "distance": distance,
        },
        origin=shell.address.origin,
    )
    assert status == 200
    assert "One prompt to try" in result
    semantic_key = shell._creative_suggestion_result.semantic_key
    title = shell._creative_suggestion_result.title
    assert semantic_key not in result
    return result, title


def test_not_now_persists_across_relaunch_and_restore_is_reversible(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_song(data_root)

    shell = new_shell(data_root, state_root, 13101, "not-now")
    try:
        result, title = prepare(shell)
        assert "Later this Song" in result
        assert "After release" in result
        assert "Next Song" in result
        assert "Someday" in result
        assert "Never suggest this again" in result
        assert "explicit Song-release evidence" in result

        status, deferred = post(
            shell,
            "/suggestion/defer",
            {
                "csrf": shell._csrf,
                "action": action_for(result, "/suggestion/defer"),
                "horizon": "NEXT_SONG",
            },
            origin=shell.address.origin,
        )
        assert status == 200
        assert "Not Now remembered: Next Song" in deferred
        assert "Deferred suggestions" in deferred
        assert title in deferred
        assert "Bring it back" in deferred
        assert "SUGGESTION:" not in deferred
        assert getattr(shell, "_creative_suggestion_result", None) is None
    finally:
        shell.stop()

    relaunched = new_shell(data_root, state_root, 13102, "not-now-relaunch")
    try:
        status, page = get(relaunched, "/song")
        assert status == 200
        assert "Deferred suggestions" in page
        assert title in page
        assert "Bring it back" in page
        assert "SUGGESTION:" not in page

        status, restored = post(
            relaunched,
            "/suggestion/restore",
            {
                "csrf": relaunched._csrf,
                "action": action_for(page, "/suggestion/restore"),
            },
            origin=relaunched.address.origin,
        )
        assert status == 200
        assert "can be suggested again" in restored
        assert "Deferred suggestions" not in restored
    finally:
        relaunched.stop()


def test_defer_rejects_foreign_origin_without_consuming_action(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_song(data_root)
    shell = new_shell(data_root, state_root, 13103, "not-now-origin")
    try:
        result, _ = prepare(shell)
        token = action_for(result, "/suggestion/defer")
        fields = {
            "csrf": shell._csrf,
            "action": token,
            "horizon": "SOMEDAY",
        }
        status, _ = post(
            shell, "/suggestion/defer", fields, origin="https://example.invalid"
        )
        assert status == 403
        assert shell.runtime.headquarters.attention_deferrals.active_items() == ()

        status, valid = post(
            shell, "/suggestion/defer", fields, origin=shell.address.origin
        )
        assert status == 200
        assert "Not Now remembered: Someday" in valid
    finally:
        shell.stop()


def test_stale_song_context_cannot_defer_old_suggestion(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_song(data_root)
    shell = new_shell(data_root, state_root, 13104, "not-now-stale")
    try:
        result, _ = prepare(shell)
        defer_token = action_for(result, "/suggestion/defer")

        start_action = shell._new_action("song-start")
        status, _ = post(
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

        status, blocked = post(
            shell,
            "/suggestion/defer",
            {
                "csrf": shell._csrf,
                "action": defer_token,
                "horizon": "SOMEDAY",
            },
            origin=shell.address.origin,
        )
        assert status == 409
        assert "Song or suggestion context changed" in blocked
        assert shell.runtime.headquarters.attention_deferrals.active_items() == ()
    finally:
        shell.stop()


def test_never_suggest_again_is_durable_but_not_irreversible(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_song(data_root)
    shell = new_shell(data_root, state_root, 13105, "not-now-never")
    try:
        result, title = prepare(shell, distance="WILDCARD")
        first_key = shell._creative_suggestion_result.semantic_key
        status, deferred = post(
            shell,
            "/suggestion/defer",
            {
                "csrf": shell._csrf,
                "action": action_for(result, "/suggestion/defer"),
                "horizon": "NEVER_SUGGEST_AGAIN",
            },
            origin=shell.address.origin,
        )
        assert status == 200
        assert "Never suggest this again" in deferred

        _, new_page = get(shell, "/song")
        status, next_result = post(
            shell,
            "/suggestion/create",
            {
                "csrf": shell._csrf,
                "action": action_for(new_page, "/suggestion/create"),
                "distance": "WILDCARD",
            },
            origin=shell.address.origin,
        )
        assert status == 200
        assert shell._creative_suggestion_result.semantic_key != first_key
        assert title not in next_result or shell._creative_suggestion_result.title != title

        status, restored = post(
            shell,
            "/suggestion/restore",
            {
                "csrf": shell._csrf,
                "action": action_for(next_result, "/suggestion/restore"),
            },
            origin=shell.address.origin,
        )
        assert status == 200
        assert "can be suggested again" in restored
    finally:
        shell.stop()
