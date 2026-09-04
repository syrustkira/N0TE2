from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from n0te2.consumer_shell import ConsumerShell
from n0te2.instance import ProcessIdentity
from n0te2.job_entry_shell import install_progressive_job_entry
from n0te2.memory import HeadquartersMemory
from n0te2.platforms import PlatformEnvironment


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
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Origin"] = shell.address.origin
    req = Request(shell.address.origin + path, data=data, headers=headers, method=method)
    try:
        with build_opener(NoRedirect()).open(req, timeout=2.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def choose_job(page: str, label: str) -> Form:
    parser = Parser()
    parser.feed(page)
    matches = [
        form
        for form in parser.forms
        if form.action == "/focus/set" and label in form.text
    ]
    assert len(matches) == 1
    return matches[0]


def post_form(shell: ConsumerShell, form: Form) -> tuple[int, str]:
    return request(shell, form.action, method="POST", fields=dict(form.values))


def test_home_does_not_call_stale_song_focus_current_and_rebinds_explicitly(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    headquarters = HeadquartersMemory.create(data_root, "Context Artist")
    try:
        song_a = headquarters.store.create_song("Song A")
        original_focus = headquarters.attention.start_focus("MAKE", song_id=song_a.id)
        song_b = headquarters.store.create_song("Song B")
        profile_id = headquarters.store.profile_id
    finally:
        headquarters.close()

    install_progressive_job_entry()
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=ProcessIdentity.from_start_token(
            PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
            pid=9304,
            start_token="job-entry-context-9304",
        ),
        probe=Probe(),
    )
    shell.start()
    try:
        status, home = request(shell, "/")
        assert status == 200
        assert "Song B" in home
        assert "Choose Make" in home
        make = choose_job(home, "Choose Make")

        status, _ = post_form(shell, make)
        assert status == 303

        reopened = HeadquartersMemory.open(data_root, profile_id)
        try:
            current_focus = reopened.attention.active_focus()
            history = reopened.attention.history()
            active_song = reopened.store.active_song()
        finally:
            reopened.close()

        assert active_song is not None and active_song.id == song_b.id
        assert current_focus is not None
        assert current_focus.mode == "MAKE"
        assert current_focus.song_id == song_b.id
        assert current_focus.id != original_focus.id
        old = next(item for item in history if item.id == original_focus.id)
        assert old.state == "ENDED"
        assert old.end_reason == "SWITCHED"
    finally:
        shell.stop()
        assert shell.wait_stopped(timeout=2.0)
