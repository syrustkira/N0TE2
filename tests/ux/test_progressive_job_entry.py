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


def choose_job(page: str, label: str) -> Form:
    matches = [form for form in forms(page, "/focus/set") if label in form.text]
    assert len(matches) == 1
    return matches[0]


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


def post_form(shell: ConsumerShell, form: Form) -> tuple[int, str]:
    return request(
        shell,
        form.action,
        method="POST",
        fields=dict(form.values),
        origin=shell.address.origin,
    )


def create_profile(root: Path, artist: str, song: str | None = None) -> tuple[str, str | None]:
    headquarters = HeadquartersMemory.create(root, artist)
    try:
        created = None if song is None else headquarters.store.create_song(song)
        return headquarters.store.profile_id, None if created is None else created.id
    finally:
        headquarters.close()


def read_focus(root: Path, profile_id: str):
    headquarters = HeadquartersMemory.open(root, profile_id)
    try:
        return headquarters.attention.active_focus(), headquarters.store.active_song()
    finally:
        headquarters.close()


def start_shell(tmp_path: Path, *, artist: str, song: str | None, pid: int):
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, song_id = create_profile(data_root, artist, song)
    install_progressive_job_entry()
    install_progressive_job_entry()  # installer must remain idempotent
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(pid, f"job-entry-{pid}"),
        probe=Probe(),
    )
    shell.start()
    return shell, data_root, profile_id, song_id


def close_shell(shell: ConsumerShell) -> None:
    status, settings = request(shell, "/settings")
    assert status == 200
    quit_forms = forms(settings, "/quit")
    assert len(quit_forms) == 1
    status, closed = post_form(shell, quit_forms[0])
    assert status == 200
    assert "N0TE closed safely." in closed
    assert shell.wait_stopped(timeout=2.0)


def test_home_starts_artist_wide_jobs_without_song_or_setup(tmp_path: Path) -> None:
    shell, data_root, profile_id, _ = start_shell(
        tmp_path,
        artist="Progressive Artist",
        song=None,
        pid=9301,
    )
    status, home = request(shell, "/")
    assert status == 200
    assert home.count("What are you here to do?") == 1
    assert all(f"Choose {label}" in home for label in ("Make", "Finish", "Manage", "Release", "Perform"))
    assert "No DAW, AI provider, account, service, send, publish, purchase, or external write is required or authorized by this choice." in home
    assert "You can choose any Artist-wide job now." in home
    assert "Start or select a Song before choosing Make or Finish if you want that Focus bound to the Song." in home

    status, _ = post_form(shell, choose_job(home, "Choose Manage"))
    assert status == 303
    focus, active_song = read_focus(data_root, profile_id)
    assert focus is not None
    assert focus.mode == "MANAGE"
    assert focus.song_id is None
    assert active_song is None

    status, resumed = request(shell, "/")
    assert status == 200
    assert resumed.count("What are you here to do?") == 1
    assert "Manage Focus" in resumed
    assert "Continue" in resumed
    close_shell(shell)


def test_home_finish_job_binds_to_exact_active_song(tmp_path: Path) -> None:
    shell, data_root, profile_id, song_id = start_shell(
        tmp_path,
        artist="Finish Artist",
        song="Exact Song",
        pid=9302,
    )
    assert song_id is not None
    status, home = request(shell, "/")
    assert status == 200
    assert "Make and Finish follow <strong>Exact Song</strong>." in home
    assert "Manage, Release and Perform stay Artist-wide" in home

    status, _ = post_form(shell, choose_job(home, "Choose Finish"))
    assert status == 303
    focus, active_song = read_focus(data_root, profile_id)
    assert focus is not None
    assert focus.mode == "FINISH"
    assert focus.song_id == song_id
    assert active_song is not None and active_song.id == song_id

    status, now = request(shell, "/now")
    assert status == 200
    assert "Finish Focus active" in now
    assert "Exact Song" in now
    close_shell(shell)


def test_home_job_entry_reuses_existing_origin_csrf_and_replay_authority(tmp_path: Path) -> None:
    shell, data_root, profile_id, _ = start_shell(
        tmp_path,
        artist="Authority Artist",
        song="Authority Song",
        pid=9303,
    )
    status, home = request(shell, "/")
    assert status == 200
    release_form = choose_job(home, "Choose Release")

    # Foreign Origin is rejected before body parsing, matching the existing
    # Focus transport contract and avoiding the historical Windows unread-body
    # socket artifact in the security assertion itself.
    status, rejected = request(
        shell,
        release_form.action,
        method="POST",
        origin="https://attacker.example",
    )
    assert status == 403
    assert "That action did not come from this N0TE window." in rejected
    focus, _ = read_focus(data_root, profile_id)
    assert focus is None

    wrong_csrf = dict(release_form.values)
    wrong_csrf["csrf"] = "wrong"
    status, rejected = request(
        shell,
        release_form.action,
        method="POST",
        fields=wrong_csrf,
        origin=shell.address.origin,
    )
    assert status == 403
    assert "That action expired. Reload N0TE and try again." in rejected
    focus, _ = read_focus(data_root, profile_id)
    assert focus is None

    status, _ = post_form(shell, release_form)
    assert status == 303
    status, replayed = post_form(shell, release_form)
    assert status == 409
    assert "already handled or expired" in replayed
    focus, _ = read_focus(data_root, profile_id)
    assert focus is not None
    assert focus.mode == "RELEASE"
    assert focus.song_id is None
    close_shell(shell)
