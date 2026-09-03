from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

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


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass
class Form:
    action: str
    values: dict[str, str]
    text: str = ""


class Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[Form] = []
        self.current: Form | None = None

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        values = dict(attrs)
        if tag == "form":
            self.current = Form(str(values.get("action", "")), {})
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and values.get("name"):
            self.current.values[str(values["name"])] = str(values.get("value", ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.text += data


def forms(page: str, action: str) -> list[Form]:
    parser = Parser()
    parser.feed(page)
    return [candidate for candidate in parser.forms if candidate.action == action]


def request(
    shell: ConsumerShell,
    path: str,
    *,
    method: str = "GET",
    fields: dict[str, str] | None = None,
    origin: str | None = None,
) -> tuple[int, str]:
    headers: dict[str, str] = {}
    data = None
    if fields is not None:
        data = urlencode(fields).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if origin is not None:
        headers["Origin"] = origin
    req = Request(shell.address.origin + path, data=data, headers=headers, method=method)
    try:
        with build_opener(NoRedirect()).open(req, timeout=2.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def post(shell: ConsumerShell, form: Form, *, origin: str | None = None) -> tuple[int, str]:
    return request(
        shell,
        form.action,
        method="POST",
        fields=dict(form.values),
        origin=shell.address.origin if origin is None else origin,
    )


def quit_shell(shell: ConsumerShell) -> None:
    _, settings = request(shell, "/settings")
    quit_form = forms(settings, "/quit")[0]
    status, _ = post(shell, quit_form)
    assert status == 200
    assert shell.wait_stopped(timeout=2.0)


def prepare_profile(root: Path) -> tuple[str, str]:
    hq = HeadquartersMemory.create(root, "Not Now Artist")
    try:
        song = hq.store.create_song("Keep Moving")
        session = hq.sessions.start_session(song_id=song.id, objective="Finish the chorus decision")
        hq.sessions.close_session(
            session.id,
            debrief_summary="The shorter lift works.",
            next_action="Print the chorus alt and compare it.",
        )
        return hq.store.profile_id, song.id
    finally:
        hq.close()


def test_now_thread_can_be_deferred_restored_and_survives_relaunch(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, _ = prepare_profile(data_root)
    proc = process(9201, "ux01-not-now")
    shell = ConsumerShell(data_root=data_root, state_root=state_root, process=proc, probe=Probe())
    shell.start()

    status, now = request(shell, "/now")
    assert status == 200
    assert "Print the chorus alt and compare it." in now
    deferrals = forms(now, "/not-now/defer")
    assert len(deferrals) == 5
    assert {form.text.strip() for form in deferrals} == {
        "Later this Song",
        "After release",
        "Next Song",
        "Someday",
        "Never suggest again",
    }

    someday = next(form for form in deferrals if "Someday" in form.text)
    status, _ = post(shell, someday)
    assert status == 303

    status, deferred = request(shell, "/now")
    assert status == 200
    assert "Deferred · Someday" in deferred
    assert "Print the chorus alt and compare it." not in deferred
    restore = forms(deferred, "/not-now/restore")
    assert len(restore) == 1

    # The deferral is attention state only, not taste/skill evidence.
    hq = HeadquartersMemory.open(data_root, profile_id)
    try:
        active = hq.attention_deferrals.active("NOW_THREAD")
        assert active is not None and active.horizon == "SOMEDAY"
        assert hq.store._conn.execute("SELECT COUNT(*) AS n FROM evidence_claims").fetchone()["n"] == 0
    finally:
        hq.close()

    quit_shell(shell)
    relaunched = ConsumerShell(data_root=data_root, state_root=state_root, process=proc, probe=Probe())
    relaunched.start()
    _, still_deferred = request(relaunched, "/now")
    assert "Deferred · Someday" in still_deferred
    restore = forms(still_deferred, "/not-now/restore")[0]
    status, _ = post(relaunched, restore)
    assert status == 303
    _, restored = request(relaunched, "/now")
    assert "Print the chorus alt and compare it." in restored
    assert "Deferred · Someday" not in restored
    quit_shell(relaunched)


def test_not_now_is_one_shot_origin_protected_and_stale_thread_fails_closed(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    prepare_profile(data_root)
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9202, "ux01-not-now-security"),
        probe=Probe(),
    )
    shell.start()

    _, now = request(shell, "/now")
    later = next(form for form in forms(now, "/not-now/defer") if "Later this Song" in form.text)
    status, _ = post(shell, later, origin="https://attacker.example")
    assert status == 403

    status, _ = post(shell, later)
    assert status == 303
    status, _ = post(shell, later)
    assert status == 409
    _, deferred = request(shell, "/now")
    assert "Deferred · Later this Song" in deferred
    quit_shell(shell)
