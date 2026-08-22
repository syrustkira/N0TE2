#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "SONG-01" or state.get("active_increment") != "SONG-01B":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.consumer_shell import ConsumerShell  # noqa: E402
from n0te2.instance import InstanceLeaseManager, ProcessIdentity  # noqa: E402
from n0te2.platforms import PlatformEnvironment  # noqa: E402
from n0te2.shell_design import SHELL_CSS  # noqa: E402


class Probe:
    def status(self, process: ProcessIdentity) -> str:
        return "UNKNOWN"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass
class Submit:
    name: str
    value: str
    text: str = ""


@dataclass
class Form:
    action: str
    values: dict[str, str]
    text: str = ""
    buttons: list[Submit] = field(default_factory=list)


class Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[Form] = []
        self.current: Form | None = None
        self.current_button: Submit | None = None

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        values = dict(attrs)
        if tag == "form":
            self.current = Form(str(values.get("action", "")), {})
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and values.get("name"):
            self.current.values[str(values["name"])] = str(values.get("value", ""))
        elif tag == "button" and self.current is not None and values.get("name"):
            self.current_button = Submit(
                name=str(values["name"]),
                value=str(values.get("value", "")),
            )
            self.current.buttons.append(self.current_button)

    def handle_endtag(self, tag: str) -> None:
        if tag == "button":
            self.current_button = None
        elif tag == "form":
            self.current = None
            self.current_button = None

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.text += data
        if self.current_button is not None:
            self.current_button.text += data


def get(shell: ConsumerShell, path: str) -> tuple[int, str]:
    with build_opener(NoRedirect()).open(Request(shell.address.origin + path), timeout=2.0) as response:
        return response.status, response.read().decode("utf-8")


def post(shell: ConsumerShell, path: str, values: dict[str, str]) -> tuple[int, str]:
    request = Request(
        shell.address.origin + path,
        data=urlencode(values).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": shell.address.origin,
        },
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=2.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def parsed_forms(page: str, action: str) -> list[Form]:
    parser = Parser()
    parser.feed(page)
    return [candidate for candidate in parser.forms if candidate.action == action]


def form(page: str, action: str) -> Form:
    matches = parsed_forms(page, action)
    assert len(matches) == 1
    return matches[0]


def capture_values(page: str, label: str, body: str) -> dict[str, str]:
    capture = form(page, "/session/capture")
    matches = [button for button in capture.buttons if button.text.strip() == label]
    assert len(matches) == 1
    button = matches[0]
    values = dict(capture.values)
    values[button.name] = button.value
    values["body"] = body
    return values


def quit_shell(shell: ConsumerShell) -> str:
    status, settings = get(shell, "/settings")
    assert status == 200
    quit_form = form(settings, "/quit")
    status, closed = post(shell, "/quit", quit_form.values)
    assert status == 200
    assert shell.wait_stopped(timeout=2.0)
    return closed


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp).resolve()
    data_root = (root / "data").resolve()
    state_root = (root / "state").resolve()
    process = ProcessIdentity.from_start_token(
        PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
        pid=99011,
        start_token="song-01b-consumer-smoke",
    )
    probe = Probe()

    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process,
        probe=probe,
    )
    address = shell.start()
    assert address.host == "127.0.0.1"

    status, welcome = get(shell, "/")
    assert status == 200
    assert "Welcome to your Headquarters" in welcome
    create = form(welcome, "/profile/create")
    create.values["artist_name"] = "Session Capture Smoke Artist"
    status, _ = post(shell, "/profile/create", create.values)
    assert status == 303

    status, css = get(shell, "/assets/shell.css")
    assert status == 200
    assert css == SHELL_CSS
    assert "textarea:focus-visible" in css

    status, song_page = get(shell, "/song")
    start_song = form(song_page, "/song/start")
    start_song.values["song_title"] = "Session Capture Smoke Song"
    status, _ = post(shell, "/song/start", start_song.values)
    assert status == 303
    song = shell.runtime.headquarters.store.active_song()
    profile_id = shell.runtime.profile_id
    assert song is not None and profile_id is not None

    status, session_page = get(shell, "/song")
    start_session = form(session_page, "/session/start")
    start_session.values["objective"] = "Shape the chorus and remember the useful decisions"
    status, _ = post(shell, "/session/start", start_session.values)
    assert status == 303
    opened = shell.runtime.headquarters.sessions.latest_for_song(song.id)
    assert opened is not None and opened.state == "OPEN"

    captured = [
        ("Observation", "The sparse first half gives the vocal more room"),
        ("Decision", "Keep the drums dry until the final four bars"),
        ("Rejected idea", "Do not double the guitar hook an octave up"),
        ("Unresolved", "Check whether the last chord needs the vocal third"),
        ("MARK", "Bar 17 transition is worth revisiting"),
    ]
    for label, body in captured:
        status, page = get(shell, "/song")
        assert status == 200
        values = capture_values(page, label, body)
        status, _ = post(shell, "/session/capture", values)
        assert status == 303

    items = shell.runtime.headquarters.sessions.items_for_session(opened.id)
    assert [item.kind for item in items] == [
        "OBSERVATION",
        "DECISION",
        "REJECTED_IDEA",
        "UNRESOLVED",
        "MARK",
    ]
    assert [item.body for item in items] == [body for _, body in captured]
    assert all(shell.runtime.headquarters.sessions.promotion_for_item(item.id) is None for item in items)
    assert shell.runtime.headquarters.store._conn.execute(
        "SELECT COUNT(*) FROM evidence_claims"
    ).fetchone()[0] == 0

    for path in ("/", "/song", "/now", "/settings"):
        status, page = get(shell, path)
        assert status == 200
        assert "Session Capture Smoke Artist" in page
        assert "Session Capture Smoke Song" in page
        assert "sess_" not in page
        assert "sitem_" not in page
        assert "prf_" not in page
        assert "sqlite" not in page.lower()
        assert "traceback" not in page.lower()
    status, open_song_page = get(shell, "/song")
    assert "Current work Session" in open_song_page
    assert [open_song_page.index(body) for _, body in captured] == sorted(
        open_song_page.index(body) for _, body in captured
    )

    assert InstanceLeaseManager(state_root).inspect(profile_id) is not None
    closed_shell = quit_shell(shell)
    assert "N0TE closed safely." in closed_shell
    assert InstanceLeaseManager(state_root).inspect(profile_id) is None

    relaunched = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process,
        probe=probe,
    )
    relaunched.start()
    status, resumed_open = get(relaunched, "/song")
    assert status == 200
    assert "Current work Session" in resumed_open
    assert [resumed_open.index(body) for _, body in captured] == sorted(
        resumed_open.index(body) for _, body in captured
    )

    finish = form(resumed_open, "/session/finish")
    finish.values.update(
        {
            "debrief": "The chorus is shaped and the decisions survived the restart.",
            "next_action": "Resolve the last-chord vocal stack before adding layers",
        }
    )
    status, _ = post(relaunched, "/session/finish", finish.values)
    assert status == 303
    latest = relaunched.runtime.headquarters.sessions.latest_for_song(song.id)
    assert latest is not None and latest.state == "CLOSED"
    assert latest.next_action == "Resolve the last-chord vocal stack before adding layers"
    assert relaunched.runtime.headquarters.store._conn.execute(
        "SELECT COUNT(*) FROM evidence_claims"
    ).fetchone()[0] == 0

    status, closed_session_page = get(relaunched, "/song")
    assert status == 200
    assert "Last Session history" in closed_session_page
    assert parsed_forms(closed_session_page, "/session/capture") == []
    assert [closed_session_page.index(body) for _, body in captured] == sorted(
        closed_session_page.index(body) for _, body in captured
    )
    assert "Resolve the last-chord vocal stack before adding layers" in closed_session_page
    quit_shell(relaunched)

print(
    "SONG-01B CONSUMER SMOKE: GREEN: a fresh artist started a canonical Song work Session, captured ordered observations/decisions/rejected ideas/unresolved questions/MARKs, navigated and explicitly quit, relaunched with exact Session history, closed with debrief/next action, kept that history visible, hid raw IDs, and did not silently promote scratch into durable Song evidence"
)
