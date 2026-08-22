from __future__ import annotations

import sqlite3
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


def process() -> ProcessIdentity:
    return ProcessIdentity.from_start_token(
        PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
        pid=8410,
        start_token="song-session-invariants",
    )


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


def request(
    shell: ConsumerShell,
    path: str,
    *,
    method: str = "GET",
    fields: dict[str, str] | None = None,
) -> tuple[int, str]:
    headers: dict[str, str] = {}
    data = None
    if fields is not None:
        data = urlencode(fields).encode("utf-8")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": shell.address.origin,
        }
    req = Request(shell.address.origin + path, data=data, headers=headers, method=method)
    try:
        with build_opener(NoRedirect()).open(req, timeout=2.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def form(page: str, action: str) -> Form:
    parser = Parser()
    parser.feed(page)
    matches = [candidate for candidate in parser.forms if candidate.action == action]
    assert len(matches) == 1
    return matches[0]


def test_session_start_finish_does_not_mutate_song_artifact_evidence_or_execution_state(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    headquarters = HeadquartersMemory.create(data_root, "Invariant Artist")
    profile_id = headquarters.store.profile_id
    song = headquarters.store.create_song("Invariant Song")
    asset = headquarters.store.attach_asset(
        song.id,
        name="reference.wav",
        sha256="a" * 64,
        source_uri="file:///reference.wav",
    )
    version = headquarters.store.create_version(
        song.id,
        label="Before Session",
        asset_ids=(asset.id,),
    )
    before_song = headquarters.store.get_song(song.id)
    assert before_song is not None and before_song.current_version_id == version.id
    headquarters.close()

    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(),
        probe=Probe(),
    )
    shell.start()
    conn = shell.runtime.headquarters.store._conn

    def count(table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    before_counts = {
        "assets": count("assets"),
        "versions": count("versions"),
        "version_assets": count("version_assets"),
        "evidence_claims": count("evidence_claims"),
        "operations": count("operations"),
    }
    assert shell.runtime.headquarters.attention.active_focus() is None

    status, page = request(shell, "/song")
    assert status == 200
    start = form(page, "/session/start")
    fields = dict(start.values)
    fields["objective"] = "Decide whether the chorus needs another harmony"
    status, _ = request(shell, "/session/start", method="POST", fields=fields)
    assert status == 303

    status, page = request(shell, "/song")
    assert status == 200
    finish = form(page, "/session/finish")
    fields = dict(finish.values)
    fields.update(
        {
            "debrief": "The existing harmony is enough; the chorus needs space instead.",
            "next_action": "Mute the extra harmony idea and listen again tomorrow",
        }
    )
    status, _ = request(shell, "/session/finish", method="POST", fields=fields)
    assert status == 303

    assert shell.runtime.headquarters.store.get_song(song.id) == before_song
    assert shell.runtime.headquarters.store.get_asset(asset.id) == asset
    assert shell.runtime.headquarters.store.get_version(version.id) == version
    assert {
        "assets": count("assets"),
        "versions": count("versions"),
        "version_assets": count("version_assets"),
        "evidence_claims": count("evidence_claims"),
        "operations": count("operations"),
    } == before_counts
    assert shell.runtime.headquarters.attention.active_focus() is None

    latest = shell.runtime.headquarters.sessions.latest_for_song(song.id)
    assert latest is not None and latest.state == "CLOSED"
    assert latest.next_action == "Mute the extra harmony idea and listen again tomorrow"

    status, settings = request(shell, "/settings")
    quit_form = form(settings, "/quit")
    status, _ = request(shell, "/quit", method="POST", fields=quit_form.values)
    assert status == 200
    assert shell.wait_stopped(timeout=2.0)

    db = data_root / "profiles" / profile_id / "lineage.sqlite3"
    persisted = sqlite3.connect(db)
    try:
        assert persisted.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
        assert persisted.execute("SELECT COUNT(*) FROM evidence_claims").fetchone()[0] == 0
    finally:
        persisted.close()


def test_blank_next_action_keeps_open_session_unchanged(tmp_path: Path) -> None:
    data_root = (tmp_path / "data-next").resolve()
    state_root = (tmp_path / "state-next").resolve()
    headquarters = HeadquartersMemory.create(data_root, "Next Action Artist")
    song = headquarters.store.create_song("Next Action Song")
    headquarters.close()

    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=ProcessIdentity.from_start_token(
            PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
            pid=8411,
            start_token="song-session-next-action",
        ),
        probe=Probe(),
    )
    shell.start()
    status, page = request(shell, "/song")
    start = form(page, "/session/start")
    fields = dict(start.values)
    fields["objective"] = "Shape the outro"
    status, _ = request(shell, "/session/start", method="POST", fields=fields)
    assert status == 303
    opened = shell.runtime.headquarters.sessions.latest_for_song(song.id)
    assert opened is not None and opened.state == "OPEN"

    status, page = request(shell, "/song")
    finish = form(page, "/session/finish")
    fields = dict(finish.values)
    fields.update({"debrief": "The outro shape works.", "next_action": "   "})
    status, _ = request(shell, "/session/finish", method="POST", fields=fields)
    assert status == 303
    assert shell.runtime.headquarters.sessions.latest_for_song(song.id) == opened

    status, page = request(shell, "/song")
    assert "Next action must not be empty" in page
    assert "Current work Session" in page

    status, settings = request(shell, "/settings")
    quit_form = form(settings, "/quit")
    status, _ = request(shell, "/quit", method="POST", fields=quit_form.values)
    assert status == 200
    assert shell.wait_stopped(timeout=2.0)
