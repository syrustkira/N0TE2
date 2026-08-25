from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from n0te2 import HeadquartersMemory
from n0te2.consumer_shell import ConsumerShell
from n0te2.instance import ProcessIdentity
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
    button_text: str = ""


class FormParser(HTMLParser):
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
        if self.current is not None and self.in_button:
            self.current.button_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "button":
            self.in_button = False
        elif tag == "form":
            self.current = None


def forms(page: str, action: str) -> list[Form]:
    parser = FormParser()
    parser.feed(page)
    return [item for item in parser.forms if item.action == action]


def request(
    shell: ConsumerShell,
    path: str,
    *,
    method: str = "GET",
    fields: dict[str, str] | None = None,
    origin: str | None = None,
) -> tuple[int, str, str | None]:
    headers: dict[str, str] = {}
    data = None
    if fields is not None:
        data = urlencode(fields).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if origin is not None:
        headers["Origin"] = origin
    req = Request(shell.address.origin + path, data=data, method=method, headers=headers)
    try:
        with build_opener(NoRedirect()).open(req, timeout=2.0) as response:
            return response.status, response.read().decode("utf-8"), response.headers.get("Location")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), exc.headers.get("Location")


def seed_profile(data_root: Path) -> tuple[str, str]:
    hq = HeadquartersMemory.create(data_root, "Learning Artist")
    try:
        song = hq.store.create_song("Learning Song")
        session = hq.sessions.start_session(song_id=song.id, objective="Practice compression")
        hq.sessions.close_session(
            session.id,
            debrief_summary="Completed a controlled compression pass",
            next_action="Repeat the move without prompts",
        )
        evidence = hq.evidence.record_claim(
            scope_kind="SONG",
            scope_id=song.id,
            key="skill.compression",
            value="completed pass",
            source_kind="OBSERVED",
        )
        hq.skills.record_assessment(
            skill_id="Compression",
            level="PRACTICED",
            source_kind="N0TE_ASSESSED",
            source_ref="test:real-work",
            confidence=0.8,
            assistance_level=0.5,
            session_id=session.id,
            evidence_claim_ids=(evidence.id,),
        )
        return hq.store.profile_id, song.id
    finally:
        hq.close()


def test_song_surface_shows_inspectable_skill_truth_without_internal_ids(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, song_id = seed_profile(data_root)
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9801, "skill-visible"),
        probe=Probe(),
    )
    shell.start()
    try:
        status, page, _ = request(shell, "/song")
        assert status == 200
        assert page.count("<h2>What N0TE thinks you can do</h2>") == 1
        assert "Compression" in page
        assert "Practiced" in page
        assert "N0TE assessment" in page
        assert "Some assistance" in page
        assert "1 linked evidence claim" in page
        assert "Independent means you can do it without guidance" in page
        assert "skillassess_" not in page
        assert "test:real-work" not in page
        assert "claim_" not in page
        assert "evidence_claim" not in page
        before = shell.runtime.headquarters.skills.history("Compression")
        status, again, _ = request(shell, "/song")
        assert status == 200
        assert shell.runtime.headquarters.skills.history("Compression") == before
        assert again.count("<h2>What N0TE thinks you can do</h2>") == 1
    finally:
        shell.stop()

    reopened = HeadquartersMemory.open(data_root, profile_id)
    try:
        assert reopened.store.active_song().id == song_id
        assert reopened.skills.state("Compression").level == "PRACTICED"
    finally:
        reopened.close()


def test_artist_can_declare_then_correct_skill_without_rewriting_history(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, _ = seed_profile(data_root)
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9802, "skill-declare-correct"),
        probe=Probe(),
    )
    shell.start()
    try:
        status, page, _ = request(shell, "/song")
        assert status == 200
        declare = forms(page, "/skill/declare")
        assert len(declare) == 1
        fields = dict(declare[0].values)
        fields.update({"skill_name": "Arrangement", "level": "APPLIED", "assistance": "SOME"})
        status, _, location = request(
            shell,
            "/skill/declare",
            method="POST",
            fields=fields,
            origin=shell.address.origin,
        )
        assert status == 303 and location == "/song"
        first = shell.runtime.headquarters.skills.history("Arrangement")
        assert len(first) == 1
        assert first[0].source_kind == "ARTIST_DECLARED"
        assert first[0].level == "APPLIED"

        status, page, _ = request(shell, "/song")
        assert status == 200
        corrections = forms(page, "/skill/correct")
        assert len(corrections) == 2
        arrangement = next(form for form in corrections if "Arrangement" in page[page.find(form.values["action"]) - 1500 : page.find(form.values["action"]) + 1500])
        fields = dict(arrangement.values)
        fields.update({
            "level": "PRACTICED",
            "assistance": "HIGH",
            "reason": "I can practice this, but I overstated how independently I apply it.",
        })
        status, _, location = request(
            shell,
            "/skill/correct",
            method="POST",
            fields=fields,
            origin=shell.address.origin,
        )
        assert status == 303 and location == "/song"
        history = shell.runtime.headquarters.skills.history("Arrangement")
        assert len(history) == 2
        assert history[0].source_kind == "ARTIST_DECLARED"
        assert history[1].source_kind == "ARTIST_CORRECTION"
        assert history[1].level == "PRACTICED"
        assert "overstated" in (history[1].note or "")
    finally:
        shell.stop()

    reopened = HeadquartersMemory.open(data_root, profile_id)
    try:
        assert reopened.skills.state("Arrangement").level == "PRACTICED"
        assert len(reopened.skills.history("Arrangement")) == 2
    finally:
        reopened.close()


def test_skill_actions_enforce_origin_csrf_replay_and_stale_correction(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_profile(data_root)
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9803, "skill-authority"),
        probe=Probe(),
    )
    shell.start()
    try:
        status, page, _ = request(shell, "/song")
        assert status == 200
        declaration = forms(page, "/skill/declare")[0]
        foreign = dict(declaration.values)
        foreign.update({"skill_name": "EQ", "level": "PRACTICED", "assistance": "SOME"})
        status, _, _ = request(
            shell,
            "/skill/declare",
            method="POST",
            fields=foreign,
            origin="https://attacker.example",
        )
        assert status == 403
        assert shell.runtime.headquarters.skills.state("EQ").latest_assessment is None

        bad_csrf = dict(declaration.values)
        bad_csrf.update({"csrf": "wrong", "skill_name": "EQ", "level": "PRACTICED", "assistance": "SOME"})
        status, _, _ = request(
            shell,
            "/skill/declare",
            method="POST",
            fields=bad_csrf,
            origin=shell.address.origin,
        )
        assert status == 403

        good = dict(declaration.values)
        good.update({"skill_name": "EQ", "level": "PRACTICED", "assistance": "SOME"})
        status, _, _ = request(
            shell,
            "/skill/declare",
            method="POST",
            fields=good,
            origin=shell.address.origin,
        )
        assert status == 303
        status, _, _ = request(
            shell,
            "/skill/declare",
            method="POST",
            fields=good,
            origin=shell.address.origin,
        )
        assert status == 409
        assert len(shell.runtime.headquarters.skills.history("EQ")) == 1

        status, page, _ = request(shell, "/song")
        assert status == 200
        correction = next(
            form for form in forms(page, "/skill/correct")
            if "EQ" in page[page.find(form.values["action"]) - 1500 : page.find(form.values["action"]) + 1500]
        )
        current = shell.runtime.headquarters.skills.state("EQ").latest_assessment
        assert current is not None
        shell.runtime.headquarters.skills.correct_skill(
            skill_id="EQ",
            level="APPLIED",
            source_ref="test:newer-assessment",
            reason="A newer correction landed after the page rendered.",
            assistance_level=0.5,
        )
        stale = dict(correction.values)
        stale.update({"level": "INDEPENDENT", "assistance": "NONE", "reason": "stale page"})
        status, body, _ = request(
            shell,
            "/skill/correct",
            method="POST",
            fields=stale,
            origin=shell.address.origin,
        )
        assert status == 409
        assert "Skill changed" in body
        assert shell.runtime.headquarters.skills.state("EQ").level == "APPLIED"
    finally:
        shell.stop()


def test_independent_cannot_be_claimed_with_assistance_and_skill_writes_do_not_mutate_song(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, song_id = seed_profile(data_root)
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9804, "skill-independent"),
        probe=Probe(),
    )
    shell.start()
    try:
        song_before = shell.runtime.headquarters.store.get_song(song_id)
        versions_before = shell.runtime.headquarters.store.versions_for_song(song_id)
        sessions_before = shell.runtime.headquarters.sessions.latest_for_song(song_id)
        status, page, _ = request(shell, "/song")
        declaration = forms(page, "/skill/declare")[0]
        assisted = dict(declaration.values)
        assisted.update({"skill_name": "Mastering", "level": "INDEPENDENT", "assistance": "SOME"})
        status, _, _ = request(
            shell,
            "/skill/declare",
            method="POST",
            fields=assisted,
            origin=shell.address.origin,
        )
        assert status == 303
        assert shell.runtime.headquarters.skills.state("Mastering").latest_assessment is None

        status, page, _ = request(shell, "/song")
        declaration = forms(page, "/skill/declare")[0]
        independent = dict(declaration.values)
        independent.update({"skill_name": "Mastering", "level": "INDEPENDENT", "assistance": "NONE"})
        status, _, _ = request(
            shell,
            "/skill/declare",
            method="POST",
            fields=independent,
            origin=shell.address.origin,
        )
        assert status == 303
        state = shell.runtime.headquarters.skills.state("Mastering")
        assert state.level == "INDEPENDENT"
        assert state.latest_assessment is not None
        assert state.latest_assessment.assistance_level == 0.0
        assert shell.runtime.headquarters.store.get_song(song_id) == song_before
        assert shell.runtime.headquarters.store.versions_for_song(song_id) == versions_before
        assert shell.runtime.headquarters.sessions.latest_for_song(song_id) == sessions_before
    finally:
        shell.stop()

    reopened = HeadquartersMemory.open(data_root, profile_id)
    try:
        assert reopened.skills.state("Mastering").level == "INDEPENDENT"
    finally:
        reopened.close()
