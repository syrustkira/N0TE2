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


def forms(page: str, action: str) -> list[Form]:
    parser = Parser()
    parser.feed(page)
    return [candidate for candidate in parser.forms if candidate.action == action]


def form_for_status(page: str, status: str) -> Form:
    candidates = forms(page, "/people/obligation/transition")
    return next(candidate for candidate in candidates if candidate.values.get("status") == status)


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


def new_profile(root: Path) -> tuple[str, str, str]:
    hq = HeadquartersMemory.create(root, "Obligation UX Artist")
    try:
        song = hq.store.create_song("Obligation UX Song")
        person = hq.people.create_person(
            "Maya Rivera",
            relationship_context="Producer waiting on the final package",
        )
        assert hq.obligations.store is hq.store
        assert hq.obligations.people is hq.people
        assert hq.obligations.evidence is hq.evidence
        return hq.store.profile_id, song.id, person.id
    finally:
        hq.close()


def new_shell(data_root: Path, state_root: Path) -> ConsumerShell:
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(9971, "obligation-journey"),
        probe=Probe(),
    )
    shell.start()
    return shell


def test_people_obligation_journey_preserves_declared_truth_and_rejects_stale_actions(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, song_id, person_id = new_profile(data_root)
    shell = new_shell(data_root, state_root)
    try:
        status, page = request(shell, "/people")
        assert status == 200
        assert "People and open loops" in page
        assert "Maya Rivera" in page
        assert "No obligations recorded for this person" in page
        assert "This records your statement as USER_DECLARED evidence" in page

        create = forms(page, "/people/obligation/create")
        assert len(create) == 1
        create[0].values.update(
            {
                "summary": "Send the final mix package to Maya",
                "kind": "DELIVERABLE",
                "responsibility": "ARTIST_OWES",
                "due_on": "2099-09-12",
                "trigger_ref": "Final vocal approved",
                "consequence_note": "Mix handoff waits until the package arrives",
                "source_note": "I promised Maya I would send the final package",
                "bind_song": "1",
            }
        )
        status, _ = post_form(shell, create[0])
        assert status == 303

        status, page = request(shell, "/people")
        assert status == 200
        assert "People and open loops" in page
        assert "Send the final mix package to Maya" in page
        assert "Song: Obligation UX Song" in page
        assert "Timing: <strong>WAITING_FOR_TRIGGER</strong>" in page
        assert "Attention: WAITING" in page
        assert "Waiting for trigger: Final vocal approved" in page
        assert "Mix handoff waits until the package arrives" in page
        assert "I promised Maya I would send the final package" in page
        assert "USER_DECLARED" in page
        assert "source current: true" in page
        assert "PROVIDER_VERIFIED" not in page

        # Both actions come from the same render because rendering a new page
        # intentionally expires all browser action tokens.
        block = form_for_status(page, "BLOCKED")
        stale_satisfied = form_for_status(page, "SATISFIED")
        block.values["judgment_note"] = "Waiting for my final vocal approval"
        status, _ = post_form(shell, block)
        assert status == 303

        stale_satisfied.values["judgment_note"] = "This stale page should not close it"
        status, stale = post_form(shell, stale_satisfied)
        assert status == 409
        assert "changed since this page was rendered" in stale

        status, page = request(shell, "/people")
        assert status == 200
        assert "Status: BLOCKED" in page
        assert "Waiting for my final vocal approval" in page

        # Prove trigger declaration freshness with two still-live actions from
        # the same render. Reopening changes the lifecycle binding, so the
        # unused trigger action must fail as stale rather than as a replay.
        stale_trigger = forms(page, "/people/obligation/trigger")[0]
        reopen = form_for_status(page, "OPEN")
        reopen.values["judgment_note"] = "The approval path is moving again"
        status, _ = post_form(shell, reopen)
        assert status == 303

        stale_trigger.values["trigger_note"] = "This old page should not declare the trigger"
        status, stale = post_form(shell, stale_trigger)
        assert status == 409
        assert "changed since this page was rendered" in stale

        status, page = request(shell, "/people")
        assert status == 200
        assert "Status: OPEN" in page
        trigger = forms(page, "/people/obligation/trigger")
        assert len(trigger) == 1
        trigger[0].values["trigger_note"] = (
            "I am declaring that the final vocal is approved now"
        )
        status, _ = post_form(shell, trigger[0])
        assert status == 303

        status, page = request(shell, "/people")
        assert status == 200
        assert "Declared trigger evidence recorded: Final vocal approved" in page
        assert "trigger remains pending" in page
        assert "Timing: <strong>WAITING_FOR_TRIGGER</strong>" in page
        assert "I am declaring that the final vocal is approved now" in page
        assert "Declared trigger evidence" in page
        assert page.count("USER_DECLARED") >= 4
        assert "PROVIDER_VERIFIED" not in page
    finally:
        shell.stop()

    reopened = HeadquartersMemory.open(data_root, profile_id)
    try:
        obligations = reopened.obligations.for_person(person_id)
        assert len(obligations) == 1
        obligation = obligations[0]
        assert obligation.song_id == song_id
        assert obligation.person_id == person_id
        assert obligation.status == "OPEN"
        assert obligation.source_kind == "USER_DECLARED"
        assert obligation.source_truth_class == "DECLARED"
        assert [event.source_kind for event in obligation.events] == [
            "USER_DECLARED",
            "USER_DECLARED",
            "USER_DECLARED",
        ]
        assert [event.status for event in obligation.events] == ["OPEN", "BLOCKED", "OPEN"]
        assert obligation.trigger_state == "PENDING"
        assert obligation.trigger_events == ()
        assert obligation.due_state(as_of="2099-09-12") == "WAITING_FOR_TRIGGER"
        trigger_claims = reopened.evidence.active_claims(
            "SONG",
            song_id,
            f"obligation.trigger.declared.{obligation.id}",
        )
        assert len(trigger_claims) == 1
        assert trigger_claims[0].source_kind == "USER_DECLARED"
        assert trigger_claims[0].source_ref is None
        assert trigger_claims[0].value["truth_class"] == "DECLARED"
        assert trigger_claims[0].value["note"] == (
            "I am declaring that the final vocal is approved now"
        )
        assert obligation.external_action_authority_granted is False
        source = reopened.evidence.get_claim(obligation.source_claim_id)
        assert source is not None
        assert source.value["source_note"] == (
            "I promised Maya I would send the final package"
        )
    finally:
        reopened.close()
