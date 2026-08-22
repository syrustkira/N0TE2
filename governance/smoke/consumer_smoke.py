#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "SONG-01" or state.get("active_increment") != "SONG-01A":
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
        start_token="song-01a-consumer-smoke",
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
    create.values["artist_name"] = "Song Session Smoke Artist"
    status, _ = post(shell, "/profile/create", create.values)
    assert status == 303

    status, css = get(shell, "/assets/shell.css")
    assert status == 200
    assert css == SHELL_CSS
    assert "textarea:focus-visible" in css

    status, song_page = get(shell, "/song")
    start_song = form(song_page, "/song/start")
    start_song.values["song_title"] = "Song Session Smoke Song"
    status, _ = post(shell, "/song/start", start_song.values)
    assert status == 303
    song = shell.runtime.headquarters.store.active_song()
    profile_id = shell.runtime.profile_id
    assert song is not None and profile_id is not None

    status, session_page = get(shell, "/song")
    assert status == 200
    assert "Start this work Session" in session_page
    start_session = form(session_page, "/session/start")
    start_session.values["objective"] = "Finish the chorus arrangement before mixing"
    status, _ = post(shell, "/session/start", start_session.values)
    assert status == 303
    opened = shell.runtime.headquarters.sessions.latest_for_song(song.id)
    assert opened is not None and opened.state == "OPEN"
    assert opened.objective == "Finish the chorus arrangement before mixing"

    for path in ("/", "/song", "/now", "/settings"):
        status, page = get(shell, path)
        assert status == 200
        assert "Song Session Smoke Artist" in page
        assert "Song Session Smoke Song" in page
        assert "sess_" not in page
        assert "prf_" not in page
        assert "sqlite" not in page.lower()
        assert "traceback" not in page.lower()
    status, open_song_page = get(shell, "/song")
    assert "Current work Session" in open_song_page
    assert "Finish the chorus arrangement before mixing" in open_song_page

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
    assert "Finish the chorus arrangement before mixing" in resumed_open
    finish = form(resumed_open, "/session/finish")
    finish.values.update(
        {
            "debrief": "The chorus is arranged; a counterline would overcrowd the vocal.",
            "next_action": "Record the lead vocal before touching the mix",
        }
    )
    status, _ = post(relaunched, "/session/finish", finish.values)
    assert status == 303
    latest = relaunched.runtime.headquarters.sessions.latest_for_song(song.id)
    assert latest is not None and latest.state == "CLOSED"
    assert latest.next_action == "Record the lead vocal before touching the mix"
    evidence_count = relaunched.runtime.headquarters.store._conn.execute(
        "SELECT COUNT(*) FROM evidence_claims WHERE key='next.action'"
    ).fetchone()[0]
    assert evidence_count == 0

    status, closed_session_page = get(relaunched, "/song")
    assert status == 200
    assert "Pick up the thread" in closed_session_page
    assert "The chorus is arranged; a counterline would overcrowd the vocal." in closed_session_page
    assert "Record the lead vocal before touching the mix" in closed_session_page
    assert "sess_" not in closed_session_page
    quit_shell(relaunched)

    resumed_again = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process,
        probe=probe,
    )
    resumed_again.start()
    status, final_resume = get(resumed_again, "/song")
    assert status == 200
    assert "Pick up the thread" in final_resume
    assert "Record the lead vocal before touching the mix" in final_resume
    quit_shell(resumed_again)

print(
    "SONG-01A CONSUMER SMOKE: GREEN: a fresh artist started a canonical Song work Session with an objective, navigated and explicitly quit, relaunched to the same open Session, finished with a debrief and concrete next action, relaunched again to meaningful Song-level continuity, kept raw IDs hidden, and did not silently promote the Session next action into global evidence"
)
