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
from n0te2.release_readiness import ReleaseReadinessMemory


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
    button_text: str = ""


class Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[Form] = []
        self.current: Form | None = None
        self.in_button = False

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        values = dict(attrs)
        if tag == "form":
            self.current = Form(str(values.get("action", "")), {})
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and values.get("name"):
            self.current.values[str(values["name"])] = str(values.get("value", ""))
        elif tag == "button" and self.current is not None:
            self.in_button = True

    def handle_data(self, data: str) -> None:
        if self.in_button and self.current is not None:
            self.current.button_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "button":
            self.in_button = False
        elif tag == "form":
            self.current = None
            self.in_button = False


def forms(page: str, action: str) -> list[Form]:
    parser = Parser()
    parser.feed(page)
    return [candidate for candidate in parser.forms if candidate.action == action]


def form_with_button(page: str, action: str, phrase: str) -> Form:
    matches = [
        form
        for form in forms(page, action)
        if phrase.casefold() in " ".join(form.button_text.split()).casefold()
    ]
    assert len(matches) == 1, (action, phrase, [form.button_text for form in forms(page, action)])
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


def post_form(
    shell: ConsumerShell,
    form: Form,
    *,
    origin: str | None = None,
) -> tuple[int, str]:
    return request(
        shell,
        form.action,
        method="POST",
        fields=dict(form.values),
        origin=shell.address.origin if origin is None else origin,
    )


def seed_profile(root: Path) -> tuple[str, str, str]:
    hq = HeadquartersMemory.create(root, "Release UX Artist")
    try:
        song = hq.store.create_song("Release UX Song")
        version = hq.store.create_version(song.id, label="Artist-approved candidate")
        hq.store.approve_version(song.id, version.id)
        return hq.store.profile_id, song.id, version.id
    finally:
        hq.close()


def new_shell(data_root: Path, state_root: Path, pid: int, token: str) -> ConsumerShell:
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(pid, token),
        probe=Probe(),
    )
    shell.start()
    return shell


def create_plan(shell: ConsumerShell, target_on: str) -> None:
    status, page = request(shell, "/release")
    assert status == 200
    form = forms(page, "/release/plan")[0]
    form.values["target_on"] = target_on
    assert post_form(shell, form)[0] == 303


def test_release_consumer_completes_local_backward_plan_and_preserves_target_lineage(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, song_id, version_id = seed_profile(data_root)
    shell = new_shell(data_root, state_root, 12301, "release-journey")
    try:
        status, page = request(shell, "/release")
        assert status == 200
        assert "Release readiness" in page
        assert "No distribution, scheduling, publishing, sending, pitching, spending" in page
        assert "provider-confirmed or scheduled release date" in page
        assert "release_" not in page
        assert "reldel_" not in page
        assert "relmile_" not in page
        assert "song_" not in page
        assert version_id not in page

        plan_form = forms(page, "/release/plan")[0]
        plan_form.values["target_on"] = "2026-11-30"
        assert post_form(shell, plan_form)[0] == 303

        status, page = request(shell, "/release")
        assert status == 200
        assert "2026-11-30" in page
        assert "Approved Version prerequisite is present" in page
        assert "Artist-approved candidate" in page
        assert "Approval is not delivery" in page
        assert "No required deliverables are defined yet" in page
        assert "Not enough local readiness truth yet" in page

        deliverable = forms(page, "/release/deliverable")[0]
        deliverable.values.update(
            {
                "kind": "MASTER_FILE",
                "label": "Final mastered WAV",
                "required": "YES",
                "state": "MISSING",
                "note": "Master export still needs final check",
            }
        )
        assert post_form(shell, deliverable)[0] == 303

        status, page = request(shell, "/release")
        assert status == 200
        assert "Final mastered WAV" in page
        assert "Missing" in page
        assert "provider acceptance" in page
        milestone = forms(page, "/release/milestone")[0]
        milestone.values.update(
            {
                "label": "Lock release package",
                "lead_days": "14",
                "note": "Artist chose this lead time",
            }
        )
        assert post_form(shell, milestone)[0] == 303

        status, page = request(shell, "/release")
        assert status == 200
        assert "2026-11-16" in page
        assert "14 days before your 2026-11-30 target" in page
        ready = form_with_button(
            page,
            "/release/deliverable/state",
            "Mark ready locally",
        )
        ready.values["note"] = "Artist records the local master as prepared"
        assert post_form(shell, ready)[0] == 303

        status, page = request(shell, "/release")
        assert status == 200
        done = form_with_button(
            page,
            "/release/milestone/state",
            "Mark done locally",
        )
        done.values["note"] = "Package lock completed locally"
        assert post_form(shell, done)[0] == 303

        status, page = request(shell, "/release")
        assert status == 200
        assert "Ready for release review" in page
        assert "not provider acceptance" in page.lower()
        assert "Schedule release" not in page
        assert "Submit pitch" not in page
        assert "Publish release" not in page
        assert "Upload to distributor" not in page

        archive = forms(page, "/release/archive")[0]
        archive.values["note"] = "Artist moved the intended release window"
        assert post_form(shell, archive)[0] == 303

        status, page = request(shell, "/release")
        assert status == 200
        assert "Prior targets" in page
        assert "Artist moved the intended release window" in page
        assert "2026-11-30" in page
        replacement = forms(page, "/release/plan")[0]
        replacement.values["target_on"] = "2026-12-15"
        assert post_form(shell, replacement)[0] == 303
    finally:
        shell.stop()

    hq = HeadquartersMemory.open(data_root, profile_id)
    try:
        memory = ReleaseReadinessMemory(hq.store)
        history = memory.plan_history(song_id)
        assert len(history) == 2
        assert history[0].target_on == "2026-11-30"
        assert history[0].state == "ARCHIVED"
        assert history[0].archived_note == "Artist moved the intended release window"
        assert history[1].target_on == "2026-12-15"
        assert history[1].state == "ACTIVE"
        assert memory.active_plan_for_song(song_id).id == history[1].id
        assert memory.snapshot(history[1].id).review_state == "UNKNOWN"
    finally:
        hq.close()


def test_release_create_rejects_foreign_origin_bad_csrf_and_replay_without_consuming_valid_authority(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, song_id, _ = seed_profile(data_root)
    shell = new_shell(data_root, state_root, 12302, "release-security")
    try:
        status, page = request(shell, "/release")
        assert status == 200
        form = forms(page, "/release/plan")[0]
        form.values["target_on"] = "2026-12-20"

        assert post_form(shell, form, origin="https://foreign.example")[0] == 403

        bad_csrf = Form(form.action, dict(form.values), form.button_text)
        bad_csrf.values["csrf"] = "not-the-browser-session-csrf"
        assert post_form(shell, bad_csrf)[0] == 403

        assert post_form(shell, form)[0] == 303
        assert post_form(shell, form)[0] == 303
        status, page = request(shell, "/release")
        assert status == 200
        assert "already handled or expired" in page
    finally:
        shell.stop()

    hq = HeadquartersMemory.open(data_root, profile_id)
    try:
        memory = ReleaseReadinessMemory(hq.store)
        history = memory.plan_history(song_id)
        assert len(history) == 1
        assert history[0].target_on == "2026-12-20"
        assert history[0].state == "ACTIVE"
    finally:
        hq.close()


def test_release_same_page_stale_action_cannot_append_after_plan_revision(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, song_id, _ = seed_profile(data_root)
    shell = new_shell(data_root, state_root, 12303, "release-stale")
    try:
        create_plan(shell, "2027-01-20")
        status, page = request(shell, "/release")
        assert status == 200
        deliverable = forms(page, "/release/deliverable")[0]
        stale_milestone = forms(page, "/release/milestone")[0]

        deliverable.values.update(
            {
                "kind": "COVER_ART",
                "label": "Cover artwork",
                "required": "YES",
                "state": "UNKNOWN",
                "note": "Needs art direction",
            }
        )
        assert post_form(shell, deliverable)[0] == 303

        stale_milestone.values.update(
            {
                "label": "This old page must not win",
                "lead_days": "10",
                "note": "Rendered before the deliverable was added",
            }
        )
        assert post_form(shell, stale_milestone)[0] == 303

        status, page = request(shell, "/release")
        assert status == 200
        assert "changed after this page was prepared" in page
        assert "This old page must not win" not in page
    finally:
        shell.stop()

    hq = HeadquartersMemory.open(data_root, profile_id)
    try:
        memory = ReleaseReadinessMemory(hq.store)
        plan = memory.active_plan_for_song(song_id)
        assert plan is not None
        assert len(memory.deliverables_for_plan(plan.id)) == 1
        assert memory.milestones_for_plan(plan.id) == ()
    finally:
        hq.close()
