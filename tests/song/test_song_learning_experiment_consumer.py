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
    label: str
    values: dict[str, str]


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[Form] = []
        self.current: Form | None = None

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        values = dict(attrs)
        if tag == "form":
            self.current = Form(
                action=str(values.get("action", "")),
                label=str(values.get("aria-label", "")),
                values={},
            )
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and values.get("name"):
            self.current.values[str(values["name"])] = str(values.get("value", ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
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


def seed_profile(data_root: Path, *, open_session: bool = True) -> tuple[str, str, str | None]:
    hq = HeadquartersMemory.create(data_root, "Learning Artist")
    try:
        song = hq.store.create_song("Learning Song")
        session_id = None
        if open_session:
            session = hq.sessions.start_session(
                song_id=song.id,
                objective="Test one deliberate creative change",
            )
            session_id = session.id
        return hq.store.profile_id, song.id, session_id
    finally:
        hq.close()


def start_fields(form: Form) -> dict[str, str]:
    values = dict(form.values)
    values.update(
        {
            "domain": "Mixing",
            "subject": "Vocal compression",
            "change": "Lengthened the attack to let more of the vocal transient through.",
        }
    )
    return values


def observation_fields(form: Form, *, observation: str = "The vocal consonants felt clearer.") -> dict[str, str]:
    values = dict(form.values)
    values.update(
        {
            "observation": observation,
            "confidence": "MEDIUM",
            "conditions": "Same vocal take and matched monitor level",
            "confounders": "I had also taken a short ear break",
        }
    )
    return values


def decision_fields(form: Form, *, decision: str = "KEEP") -> dict[str, str]:
    values = dict(form.values)
    values.update(
        {
            "decision": decision,
            "rationale": "Keep it for this vocal, but compare again in the full arrangement.",
            "confidence": "MEDIUM",
        }
    )
    return values


def test_song_surface_exposes_honest_learning_chain_without_internal_ids(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_profile(data_root)
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9901, "learning-visible"),
        probe=Probe(),
    )
    shell.start()
    try:
        status, page, _ = request(shell, "/song")
        assert status == 200
        assert page.count("<h2>What did that change teach you?</h2>") == 1
        assert "record the change, what you observed afterward, then your decision" in page
        assert "does not prove the change caused the outcome" in page
        assert len(forms(page, "/learning/start")) == 1
        assert "learn_" not in page
        assert "lobs_" not in page
        assert "ldec_" not in page
        assert "consumer-learning-observation:" not in page
        assert "sess_" not in page

        before = shell.runtime.headquarters.learning.episodes_for_song(
            shell.runtime.headquarters.store.active_song().id
        )
        status, _, _ = request(shell, "/song")
        assert status == 200
        after = shell.runtime.headquarters.learning.episodes_for_song(
            shell.runtime.headquarters.store.active_song().id
        )
        assert before == after == ()
    finally:
        shell.stop()


def test_artist_can_complete_change_observation_decision_from_song(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, song_id, session_id = seed_profile(data_root)
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9902, "learning-flow"),
        probe=Probe(),
    )
    shell.start()
    try:
        _, page, _ = request(shell, "/song")
        start = forms(page, "/learning/start")[0]
        status, _, location = request(
            shell,
            "/learning/start",
            method="POST",
            fields=start_fields(start),
            origin=shell.address.origin,
        )
        assert status == 303 and location == "/song"
        episodes = shell.runtime.headquarters.learning.episodes_for_song(song_id)
        assert len(episodes) == 1
        assert episodes[0].session_id == session_id
        assert episodes[0].change_description.startswith("Lengthened the attack")

        _, page, _ = request(shell, "/song")
        assert "Change tried:" in page
        observe = forms(page, "/learning/observe")[0]
        status, _, location = request(
            shell,
            "/learning/observe",
            method="POST",
            fields=observation_fields(observe),
            origin=shell.address.origin,
        )
        assert status == 303 and location == "/song"
        episode = shell.runtime.headquarters.learning.episodes_for_song(song_id)[0]
        assert len(episode.consequences) == 1
        assert episode.consequences[0].source_kind == "USER_DECLARED"
        assert episode.consequences[0].confidence == 0.7

        _, page, _ = request(shell, "/song")
        assert "Observed after the change:" in page
        assert "Same vocal take and matched monitor level" in page
        assert "Possible confounders:" in page
        decide = forms(page, "/learning/decide")[0]
        status, _, location = request(
            shell,
            "/learning/decide",
            method="POST",
            fields=decision_fields(decide),
            origin=shell.address.origin,
        )
        assert status == 303 and location == "/song"
        episode = shell.runtime.headquarters.learning.episodes_for_song(song_id)[0]
        assert episode.decision is not None
        assert episode.decision.decision == "KEEP"
        assert shell.runtime.headquarters.sessions.get_session(session_id).state == "OPEN"

        _, page, _ = request(shell, "/song")
        assert "Decision: Keep this change" in page
        assert not forms(page, "/learning/observe")
        assert not forms(page, "/learning/decide")
        assert "learn_" not in page and "lobs_" not in page and "ldec_" not in page
    finally:
        shell.stop()

    reopened = HeadquartersMemory.open(data_root, profile_id)
    try:
        episode = reopened.learning.episodes_for_song(song_id)[0]
        assert episode.decision is not None and episode.decision.decision == "KEEP"
    finally:
        reopened.close()


def test_learning_actions_enforce_origin_csrf_replay_and_stale_session(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    _, song_id, session_id = seed_profile(data_root)
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9903, "learning-authority"),
        probe=Probe(),
    )
    shell.start()
    try:
        _, page, _ = request(shell, "/song")
        start = forms(page, "/learning/start")[0]
        fields = start_fields(start)

        status, _, _ = request(
            shell,
            "/learning/start",
            method="POST",
            fields=fields,
            origin="https://attacker.example",
        )
        assert status == 403
        assert shell.runtime.headquarters.learning.episodes_for_song(song_id) == ()

        bad = dict(fields)
        bad["csrf"] = "wrong"
        status, _, _ = request(
            shell,
            "/learning/start",
            method="POST",
            fields=bad,
            origin=shell.address.origin,
        )
        assert status == 403

        shell.runtime.headquarters.sessions.close_session(
            session_id,
            debrief_summary="Closed before the prepared experiment began",
            next_action="Start a fresh Session",
        )
        status, body, _ = request(
            shell,
            "/learning/start",
            method="POST",
            fields=fields,
            origin=shell.address.origin,
        )
        assert status == 409
        assert "Session changed" in body
        assert shell.runtime.headquarters.learning.episodes_for_song(song_id) == ()

        status, _, _ = request(
            shell,
            "/learning/start",
            method="POST",
            fields=fields,
            origin=shell.address.origin,
        )
        assert status == 409
    finally:
        shell.stop()


def test_prepared_decision_rejects_new_unseen_observation(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    _, song_id, _ = seed_profile(data_root)
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9904, "learning-stale-decision"),
        probe=Probe(),
    )
    shell.start()
    try:
        _, page, _ = request(shell, "/song")
        start = forms(page, "/learning/start")[0]
        status, _, _ = request(
            shell,
            "/learning/start",
            method="POST",
            fields=start_fields(start),
            origin=shell.address.origin,
        )
        assert status == 303

        _, page, _ = request(shell, "/song")
        observe = forms(page, "/learning/observe")[0]
        status, _, _ = request(
            shell,
            "/learning/observe",
            method="POST",
            fields=observation_fields(observe),
            origin=shell.address.origin,
        )
        assert status == 303

        _, page, _ = request(shell, "/song")
        stale_decision = forms(page, "/learning/decide")[0]
        episode = shell.runtime.headquarters.learning.episodes_for_song(song_id)[0]
        shell.runtime.headquarters.learning.append_consequence(
            episode.id,
            observation="A second observation arrived after the decision form rendered.",
            source_kind="USER_DECLARED",
            source_ref="test:newer-learning-evidence",
            confidence=0.8,
            confounders=("Arrangement playback position changed",),
        )

        status, body, _ = request(
            shell,
            "/learning/decide",
            method="POST",
            fields=decision_fields(stale_decision),
            origin=shell.address.origin,
        )
        assert status == 409
        assert "New Learning evidence" in body
        episode = shell.runtime.headquarters.learning.episodes_for_song(song_id)[0]
        assert len(episode.consequences) == 2
        assert episode.decision is None
    finally:
        shell.stop()


def test_open_learning_history_survives_quit_and_can_be_decided_after_session_close(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, song_id, session_id = seed_profile(data_root)
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9905, "learning-restart-one"),
        probe=Probe(),
    )
    shell.start()
    try:
        _, page, _ = request(shell, "/song")
        start = forms(page, "/learning/start")[0]
        assert request(
            shell,
            "/learning/start",
            method="POST",
            fields=start_fields(start),
            origin=shell.address.origin,
        )[0] == 303
        _, page, _ = request(shell, "/song")
        observe = forms(page, "/learning/observe")[0]
        assert request(
            shell,
            "/learning/observe",
            method="POST",
            fields=observation_fields(observe),
            origin=shell.address.origin,
        )[0] == 303
        shell.runtime.headquarters.sessions.close_session(
            session_id,
            debrief_summary="Captured the observation",
            next_action="Judge the experiment after reopening",
        )
    finally:
        shell.stop()

    shell2 = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9906, "learning-restart-two"),
        probe=Probe(),
    )
    shell2.start()
    try:
        status, page, _ = request(shell2, "/song")
        assert status == 200
        assert "The vocal consonants felt clearer" in page
        assert "Open a work Session on this Song to start a new Learning experiment" in page
        assert not forms(page, "/learning/start")
        decide = forms(page, "/learning/decide")[0]
        status, _, _ = request(
            shell2,
            "/learning/decide",
            method="POST",
            fields=decision_fields(decide, decision="INCONCLUSIVE"),
            origin=shell2.address.origin,
        )
        assert status == 303
        episode = shell2.runtime.headquarters.learning.episodes_for_song(song_id)[0]
        assert episode.decision is not None and episode.decision.decision == "INCONCLUSIVE"
        assert shell2.runtime.headquarters.sessions.get_session(session_id).state == "CLOSED"
    finally:
        shell2.stop()

    reopened = HeadquartersMemory.open(data_root, profile_id)
    try:
        assert reopened.learning.episodes_for_song(song_id)[0].decision.decision == "INCONCLUSIVE"
    finally:
        reopened.close()
