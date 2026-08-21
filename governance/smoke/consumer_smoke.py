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
if state.get("active_node") != "UX-01" or state.get("active_increment") != "UX-01A":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.consumer_shell import ConsumerShell  # noqa: E402
from n0te2.instance import InstanceLeaseManager, ProcessIdentity  # noqa: E402
from n0te2.platforms import PlatformEnvironment  # noqa: E402


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


def form(page: str, action: str) -> Form:
    parser = Parser()
    parser.feed(page)
    matches = [candidate for candidate in parser.forms if candidate.action == action]
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
        start_token="ux-01a-consumer-smoke",
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
    assert "Artist name" in welcome
    assert "prf_" not in welcome
    create = form(welcome, "/profile/create")
    create.values["artist_name"] = "Front Door Artist"
    status, _ = post(shell, "/profile/create", create.values)
    assert status == 303

    status, home = get(shell, "/")
    assert status == 200
    assert "Front Door Artist" in home
    assert "What are we making today?" in home
    assert "prf_" not in home
    profile_id = shell.runtime.profile_id
    assert profile_id is not None

    status, song_page = get(shell, "/song")
    assert status == 200
    start = form(song_page, "/song/start")
    start.values["song_title"] = "Front Door Song"
    status, _ = post(shell, "/song/start", start.values)
    assert status == 303

    for path in ("/song", "/now", "/settings", "/"):
        status, page = get(shell, path)
        assert status == 200
        assert "Front Door Artist" in page
        assert "Front Door Song" in page
        assert "prf_" not in page

    assert InstanceLeaseManager(state_root).inspect(profile_id) is not None
    closed = quit_shell(shell)
    assert "N0TE closed safely." in closed
    assert InstanceLeaseManager(state_root).inspect(profile_id) is None

    relaunched = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process,
        probe=probe,
    )
    relaunched.start()
    status, resumed = get(relaunched, "/")
    assert status == 200
    assert "Pick up where you left off" in resumed
    assert "Front Door Artist" in resumed
    assert "Front Door Song" in resumed
    quit_shell(relaunched)

print(
    "UX-01A CONSUMER SMOKE: GREEN: a fresh local consumer entered N0TE through loopback-only Artist Headquarters, created an isolated Artist workspace without DAW/AI/provider setup, started a canonical Song, preserved Artist/Song context across Home/Song/Now/Settings, explicitly quit with runtime ownership released, and relaunched to the same durable Artist/Song state"
)
