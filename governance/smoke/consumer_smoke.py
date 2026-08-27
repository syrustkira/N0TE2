#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())

if state.get("product_code_authorized") is not True:
    product_files = [
        path
        for path in (repo / "n0te2").rglob("*.py")
        if path.name != "__pycache__"
    ] if (repo / "n0te2").exists() else []
    if product_files:
        raise SystemExit("STAGE SMOKE: RED: product implementation appeared early")

if (
    state.get("active_node") != "UX-01"
    or state.get("active_increment") != "UX-01-INTERACTION-01"
):
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage "
        f"{state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.consumer_shell import ConsumerShell  # noqa: E402
from n0te2.instance import ProcessIdentity  # noqa: E402
from n0te2.platforms import PlatformEnvironment  # noqa: E402


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
        elif tag == "button" and self.current is not None:
            self.current_button = Submit(
                name=str(values.get("name", "")),
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
    request = Request(shell.address.origin + path)
    try:
        with build_opener(NoRedirect()).open(request, timeout=2.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


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


def one_form(page: str, action: str) -> Form:
    matches = parsed_forms(page, action)
    assert len(matches) == 1, f"expected one {action} form, found {len(matches)}"
    return matches[0]


def mode_form(page: str, mode: str) -> Form:
    matches = [
        candidate
        for candidate in parsed_forms(page, "/interaction/depth")
        if candidate.values.get("mode") == mode
    ]
    assert len(matches) == 1, f"expected one {mode} interaction form, found {len(matches)}"
    return matches[0]


def quit_shell(shell: ConsumerShell) -> None:
    status, settings = get(shell, "/settings")
    assert status == 200, f"settings GET returned {status}: {settings[:600]}"
    quit_form = one_form(settings, "/quit")
    status, closed = post(shell, "/quit", quit_form.values)
    assert status == 200, f"quit POST returned {status}: {closed[:600]}"
    assert "N0TE closed safely." in closed
    assert shell.wait_stopped(timeout=2.0)


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp).resolve()
    data_root = (root / "data").resolve()
    state_root = (root / "state").resolve()
    process = ProcessIdentity.from_start_token(
        PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
        pid=99012,
        start_token="ux-01-interaction-consumer-smoke",
    )
    probe = Probe()

    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process,
        probe=probe,
    )

    original_ensure_runtime = shell._ensure_runtime
    original_render_state = shell._render_state

    def diagnostic_ensure_runtime():
        try:
            return original_ensure_runtime()
        except Exception as exc:
            print(
                f"SMOKE COLD START _ensure_runtime: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)
            raise

    def diagnostic_render_state(page_state, *, path: str):
        try:
            return original_render_state(page_state, path=path)
        except Exception as exc:
            print(
                f"SMOKE COLD START _render_state: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)
            raise

    shell._ensure_runtime = diagnostic_ensure_runtime
    shell._render_state = diagnostic_render_state

    address = shell.start()
    assert address.host == "127.0.0.1"

    status, welcome = get(shell, "/")
    assert status == 200, f"welcome GET returned {status}: {welcome[:1200]}"
    assert "Welcome to your Headquarters" in welcome
    create = one_form(welcome, "/profile/create")
    create.values["artist_name"] = "Interaction Smoke Artist"
    status, created = post(shell, "/profile/create", create.values)
    assert status == 303, f"profile create returned {status}: {created[:600]}"

    status, song_page = get(shell, "/song")
    assert status == 200, f"initial song GET returned {status}: {song_page[:1200]}"
    start_song = one_form(song_page, "/song/start")
    start_song.values["song_title"] = "Interaction Smoke Song"
    status, started_song = post(shell, "/song/start", start_song.values)
    assert status == 303, f"song start returned {status}: {started_song[:600]}"

    status, song_page = get(shell, "/song")
    assert status == 200, f"song/session GET returned {status}: {song_page[:1200]}"
    start_session = one_form(song_page, "/session/start")
    start_session.values["objective"] = "Test one chorus transition without changing unrelated parts"
    status, started_session = post(shell, "/session/start", start_session.values)
    assert status == 303, f"session start returned {status}: {started_session[:600]}"

    status, song_page = get(shell, "/song")
    assert status == 200, f"learning-start GET returned {status}: {song_page[:1200]}"
    learning = one_form(song_page, "/learning/start")
    learning.values.update(
        {
            "domain": "Arrangement",
            "subject": "chorus impact",
            "change": "Mute the pre-chorus kick for one bar before the chorus",
        }
    )
    status, started_learning = post(shell, "/learning/start", learning.values)
    assert status == 303, f"learning start returned {status}: {started_learning[:600]}"

    status, song_page = get(shell, "/song")
    assert status == 200, f"interaction GET returned {status}: {song_page[:1200]}"
    assert len(parsed_forms(song_page, "/interaction/depth")) == 5
    assert "How should N0TE work with you?" in song_page
    show = mode_form(song_page, "SHOW_ME")
    status, show_result = post(shell, "/interaction/depth", show.values)
    assert status == 303, f"SHOW_ME POST returned {status}: {show_result[:600]}"

    status, shown = get(shell, "/song")
    assert status == 200, f"SHOW_ME result GET returned {status}: {shown[:1200]}"
    assert "Working style: SHOW ME" in shown
    assert "read-only walkthrough" in shown
    assert "BEFORE" in shown and "AFTER" in shown
    assert "does not claim the project was modified" in shown
    assert "No consequence has been recorded yet" in shown
    assert "learn_" not in shown and "sess_" not in shown and "prf_" not in shown

    observe = one_form(shown, "/learning/observe")
    observe.values.update(
        {
            "observation": "The chorus entrance felt larger",
            "confidence": "MEDIUM",
            "conditions": "Same playback level",
            "confounders": "Arrangement contrast may also matter",
        }
    )
    status, observed = post(shell, "/learning/observe", observe.values)
    assert status == 303, f"learning observe returned {status}: {observed[:600]}"

    status, fresh = get(shell, "/song")
    assert status == 200, f"fresh-evidence GET returned {status}: {fresh[:1200]}"
    explain = mode_form(fresh, "EXPLAIN_WHY")
    status, explained_post = post(shell, "/interaction/depth", explain.values)
    assert status == 303, f"EXPLAIN_WHY POST returned {status}: {explained_post[:600]}"
    status, explained = get(shell, "/song")
    assert status == 200, f"EXPLAIN_WHY result GET returned {status}: {explained[:1200]}"
    assert "Working style: EXPLAIN WHY" in explained
    assert "The chorus entrance felt larger" in explained
    assert "artist-reported, 70% confidence" in explained
    assert "question, not established causation" in explained
    assert "changing fewer variables" in explained
    assert "choosing a teaching/collaboration mode never approves a mutation" in explained

    quit_shell(shell)

    relaunched = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process,
        probe=probe,
    )
    relaunched.start()
    status, resumed = get(relaunched, "/song")
    assert status == 200, f"relaunch song GET returned {status}: {resumed[:1200]}"
    assert "Interaction Smoke Artist" in resumed
    assert "Interaction Smoke Song" in resumed
    assert "The chorus entrance felt larger" in resumed
    assert "Working style: EXPLAIN WHY" not in resumed
    assert len(parsed_forms(resumed, "/interaction/depth")) == 5
    assert "learn_" not in resumed and "sess_" not in resumed and "prf_" not in resumed
    quit_shell(relaunched)

print(
    "UX-01-INTERACTION-01 CONSUMER SMOKE: GREEN: a fresh artist created a Song and real Learning job, selected a read-only SHOW ME walkthrough, recorded an observed consequence, selected EXPLAIN WHY and received evidence-labeled causal-humility guidance, explicitly quit/relaunched with durable Learning evidence but no persisted interaction-mode preference, and never exposed internal lineage or granted mutation authority"
)
