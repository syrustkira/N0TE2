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
lifecycle = state.get("lifecycle_state")

if lifecycle == "ACTIVE":
    if state.get("product_code_authorized") is not True:
        raise SystemExit("STAGE SMOKE: RED: active construction lacks product-code authority")
    if (
        state.get("active_node") != "UX-01"
        or state.get("active_increment") != "UX-01-CONTEXT-LIFECYCLE-01"
    ):
        raise SystemExit(
            f"STAGE SMOKE: RED: unsupported active stage "
            f"{state.get('active_node')}/{state.get('active_increment')}"
        )
elif lifecycle == "STABLE":
    if (
        state.get("active_node") is not None
        or state.get("active_increment") is not None
        or state.get("product_code_authorized") is not False
    ):
        raise SystemExit("STAGE SMOKE: RED: STABLE retained construction authority")
else:
    raise SystemExit(f"STAGE SMOKE: RED: unsupported lifecycle {lifecycle}")

from n0te2.consumer_shell import ConsumerShell  # noqa: E402
from n0te2.instance import ProcessIdentity  # noqa: E402
from n0te2.memory import HeadquartersMemory  # noqa: E402
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
        start_token="ux-01-context-lifecycle-consumer-smoke",
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
    assert status == 200, f"welcome GET returned {status}: {welcome[:1200]}"
    assert "Welcome to your Headquarters" in welcome
    create = one_form(welcome, "/profile/create")
    create.values["artist_name"] = "Retention Smoke Artist"
    status, created = post(shell, "/profile/create", create.values)
    assert status == 303, f"profile create returned {status}: {created[:600]}"

    status, song_page = get(shell, "/song")
    assert status == 200, f"initial song GET returned {status}: {song_page[:1200]}"
    start_song = one_form(song_page, "/song/start")
    start_song.values["song_title"] = "Retention Smoke Song"
    status, started_song = post(shell, "/song/start", start_song.values)
    assert status == 303, f"song start returned {status}: {started_song[:600]}"

    status, song_page = get(shell, "/song")
    assert status == 200, f"song/session GET returned {status}: {song_page[:1200]}"
    assert "What N0TE remembers" in song_page
    assert "Retention active" in song_page
    start_session = one_form(song_page, "/session/start")
    session_objective = "Test one chorus transition without changing unrelated parts"
    start_session.values["objective"] = session_objective
    status, started_session = post(shell, "/session/start", start_session.values)
    assert status == 303, f"session start returned {status}: {started_session[:600]}"

    status, song_page = get(shell, "/song")
    assert status == 200, f"learning-start GET returned {status}: {song_page[:1200]}"
    assert "What N0TE remembers" in song_page
    assert session_objective in song_page
    assert "Work Session still open" in song_page
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
    assert "What N0TE remembers" in shown
    assert "learn_" not in shown and "sess_" not in shown and "prf_" not in shown

    observe = one_form(shown, "/learning/observe")
    observation_text = "The chorus entrance felt larger"
    observe.values.update(
        {
            "observation": observation_text,
            "confidence": "MEDIUM",
            "conditions": "Same playback level",
            "confounders": "Arrangement contrast may also matter",
        }
    )
    status, observed = post(shell, "/learning/observe", observe.values)
    assert status == 303, f"learning observe returned {status}: {observed[:600]}"

    status, fresh = get(shell, "/song")
    assert status == 200, f"fresh-evidence GET returned {status}: {fresh[:1200]}"
    assert "What N0TE remembers" in fresh
    assert "1 Learning episode" in fresh
    explain = mode_form(fresh, "EXPLAIN_WHY")
    status, explained_post = post(shell, "/interaction/depth", explain.values)
    assert status == 303, f"EXPLAIN_WHY POST returned {status}: {explained_post[:600]}"
    status, explained = get(shell, "/song")
    assert status == 200, f"EXPLAIN_WHY result GET returned {status}: {explained[:1200]}"
    assert "Working style: EXPLAIN WHY" in explained
    assert observation_text in explained
    assert "artist-reported, 70% confidence" in explained
    assert "question, not established causation" in explained
    assert "changing fewer variables" in explained
    assert "Choosing a teaching/collaboration mode never approves a mutation" in explained
    assert "What N0TE remembers" in explained
    assert "read-only" in explained
    assert "one kept result does not become permanent taste doctrine or a causal rule" in explained

    profile_id = shell.runtime.profile_id
    assert profile_id
    quit_shell(shell)

    # ConsumerShell owns its SQLite connection on the HTTP server thread. Open a
    # fresh read/composition root on this thread for the context-projection proof
    # instead of reaching across SQLite thread ownership.
    with HeadquartersMemory.open(data_root, profile_id) as retained:
        active_song = retained.store.active_song()
        assert active_song is not None
        projection = retained.context_projection.projection_for_song(
            active_song.id,
            purpose="Resume the smoke-test work without flattening canonical history",
            sections=("SESSIONS", "LEARNING", "DURABLE_FACTS"),
        )
        assert projection["schema"] == "n0te.context-projection.v1"
        assert projection["authority_ceiling"] == "READ_ONLY_CONTEXT"
        assert projection["mutation_policy"]["grants_action_authority"] is False
        assert projection["budget"]["canonical_history_deleted"] is False
        assert len(projection["source_digest"]) == 64

    relaunched = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process,
        probe=probe,
    )
    relaunched.start()
    status, resumed = get(relaunched, "/song")
    assert status == 200, f"relaunch song GET returned {status}: {resumed[:1200]}"
    assert "Retention Smoke Artist" in resumed
    assert "Retention Smoke Song" in resumed
    assert observation_text in resumed
    assert "What N0TE remembers" in resumed
    assert "Retention active" in resumed
    assert session_objective in resumed
    assert "Work Session still open" in resumed
    assert "1 Learning episode" in resumed
    assert "Working style: EXPLAIN WHY" not in resumed
    assert len(parsed_forms(resumed, "/interaction/depth")) == 5
    assert "learn_" not in resumed and "sess_" not in resumed and "prf_" not in resumed
    quit_shell(relaunched)

print(
    "UX-01-CONTEXT-LIFECYCLE-01 CONSUMER SMOKE: GREEN: a fresh artist created a Song and real work Session, used the inherited interaction-depth Learning journey, recorded evidence, saw N0TE consult canonical retention and create a bounded read-only source-digest context projection without deleting history or granting mutation authority, explicitly quit/relaunched, and recovered the same Session objective and Learning evidence while the transient interaction-mode choice correctly did not persist"
)
