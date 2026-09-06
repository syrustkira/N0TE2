from __future__ import annotations

import html
import uuid
from datetime import date
from http.server import BaseHTTPRequestHandler
from typing import Callable, Mapping

from . import consumer_shell as consumer_shell_module
from .consumer_shell import ConsumerShell, ConsumerShellError, _PageState
from .lineage import NotFoundError, ValidationError
from .obligations import OBLIGATION_KINDS, OBLIGATION_STATUSES, ObligationSnapshot
from .people import FOLLOWUP_RESPONSIBILITIES, FollowUp, Person

_MAX_PERSON_NAME = 160
_MAX_RELATIONSHIP_CONTEXT = 800
_MAX_FOLLOWUP_SUMMARY = 600
_MAX_RESOLUTION_NOTE = 1000
_MAX_OBLIGATION_SUMMARY = 1200
_MAX_OBLIGATION_SOURCE_NOTE = 1200
_MAX_OBLIGATION_TRIGGER = 800
_MAX_OBLIGATION_CONSEQUENCE = 1200
_MAX_OBLIGATION_JUDGMENT_NOTE = 1200

_RESPONSIBILITY_LABELS = {
    "ARTIST_OWES": "I owe this",
    "WAITING_ON_OTHER": "Waiting on them",
    "MUTUAL": "We both have a next step",
}

_OBLIGATION_KIND_LABELS = {
    "DELIVERABLE": "Deliverable",
    "DEADLINE": "Deadline",
    "LICENSE": "License",
    "PAYMENT": "Payment",
    "OTHER": "Other obligation",
}

_OBLIGATION_STATUS_LABELS = {
    "OPEN": "Open",
    "BLOCKED": "Blocked",
    "DISPUTED": "Disputed",
    "SATISFIED": "Satisfied",
    "WAIVED": "Waived",
    "CANCELED": "Canceled",
}


def _clean_human_text(value: str, field: str, *, maximum: int) -> str:
    text = " ".join(str(value).split())
    if not text:
        raise ConsumerShellError(f"{field} must not be empty")
    if len(text) > maximum:
        raise ConsumerShellError(f"{field} is too long")
    return text


def _optional_human_text(value: str | None, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if len(text) > maximum:
        raise ConsumerShellError(f"{field} is too long")
    return text


def _person_action(shell: ConsumerShell) -> str:
    return shell._new_action("people-create")


def _followup_action(shell: ConsumerShell, person: Person) -> str:
    song = shell.runtime.headquarters.store.active_song()
    song_marker = "~" if song is None else song.id
    return shell._new_action("people-followup-create", f"{person.id}|{song_marker}")


def _close_action(shell: ConsumerShell, followup: FollowUp, kind: str) -> str:
    return shell._new_action(kind, followup.id)


def _obligation_create_action(shell: ConsumerShell, person: Person) -> str:
    song = shell.runtime.headquarters.store.active_song()
    song_marker = "~" if song is None else song.id
    return shell._new_action("people-obligation-create", f"{person.id}|{song_marker}")


def _latest_event_sequence(obligation: ObligationSnapshot) -> int:
    if not obligation.events:
        raise ConsumerShellError("Obligation lifecycle evidence is missing")
    return obligation.events[-1].sequence


def _latest_trigger_sequence(obligation: ObligationSnapshot) -> str:
    if not obligation.trigger_events:
        return "~"
    return str(obligation.trigger_events[-1].sequence)


def _declared_trigger_key(obligation: ObligationSnapshot) -> str:
    return f"obligation.trigger.declared.{obligation.id}"


def _declared_trigger_claims(shell: ConsumerShell, obligation: ObligationSnapshot):
    hq = shell.runtime.headquarters
    scope_kind = "SONG" if obligation.song_id is not None else "ARTIST"
    scope_id = obligation.song_id if obligation.song_id is not None else hq.store.primary_artist_id
    claims = hq.evidence.active_claims(scope_kind, scope_id, _declared_trigger_key(obligation))
    if any(claim.source_kind != "USER_DECLARED" for claim in claims):
        raise ConsumerShellError("Declared trigger evidence crossed its truth boundary")
    return claims


def _latest_declared_trigger_sequence(shell: ConsumerShell, obligation: ObligationSnapshot) -> str:
    claims = _declared_trigger_claims(shell, obligation)
    if not claims:
        return "~"
    return str(claims[-1].sequence)


def _obligation_transition_action(
    shell: ConsumerShell,
    obligation: ObligationSnapshot,
    target_status: str,
) -> str:
    value = f"{obligation.id}|{_latest_event_sequence(obligation)}|{obligation.status}"
    return shell._new_action(f"people-obligation-{target_status.lower()}", value)


def _obligation_trigger_action(shell: ConsumerShell, obligation: ObligationSnapshot) -> str:
    value = (
        f"{obligation.id}|{_latest_event_sequence(obligation)}|"
        f"{obligation.status}|{_latest_trigger_sequence(obligation)}|"
        f"{_latest_declared_trigger_sequence(shell, obligation)}"
    )
    return shell._new_action("people-obligation-trigger", value)


def _person_form(shell: ConsumerShell) -> str:
    return (
        '<div class="card"><h2>Add someone you work with</h2>'
        '<p>Keep the relationship context here without pretending a provider account proves identity. N0TE will not merge this person with email, Contacts, social or credits automatically.</p>'
        '<form class="stack" method="post" action="/people/create" aria-label="Add a person">'
        f'{shell._hidden(_person_action(shell))}'
        f'<div><label for="person-name">Name</label><input id="person-name" name="display_name" type="text" maxlength="{_MAX_PERSON_NAME}" autocomplete="off" required></div>'
        f'<div><label for="person-context">Relationship context <span class="muted">optional</span></label><textarea id="person-context" name="relationship_context" maxlength="{_MAX_RELATIONSHIP_CONTEXT}" rows="3" placeholder="Producer on the EP, venue contact, manager candidate..."></textarea></div>'
        '<button class="primary" type="submit">Add person</button>'
        '<p class="muted">Local record only. This does not send, invite, connect, merge or sync anything.</p>'
        '</form></div>'
    )


def _responsibility_options() -> str:
    return "".join(
        f'<option value="{html.escape(value, quote=True)}">{html.escape(_RESPONSIBILITY_LABELS[value])}</option>'
        for value in ("ARTIST_OWES", "WAITING_ON_OTHER", "MUTUAL")
    )


def _obligation_kind_options() -> str:
    return "".join(
        f'<option value="{html.escape(value, quote=True)}">{html.escape(_OBLIGATION_KIND_LABELS[value])}</option>'
        for value in OBLIGATION_KINDS
    )


def _followup_form(shell: ConsumerShell, person: Person) -> str:
    song = shell.runtime.headquarters.store.active_song()
    song_binding = ""
    if song is not None:
        song_binding = (
            '<label><input type="checkbox" name="bind_song" value="1"> '
            f'Bind this follow-up to current Song: <strong>{html.escape(song.title)}</strong></label>'
        )
    return (
        '<form class="stack" method="post" action="/people/followup/create" '
        f'aria-label="Add follow-up for {html.escape(person.display_name, quote=True)}">'
        f'{shell._hidden(_followup_action(shell, person))}'
        f'<div><label>Follow-up<textarea name="summary" maxlength="{_MAX_FOLLOWUP_SUMMARY}" rows="3" required></textarea></label></div>'
        '<div><label>Who has the next move?<select name="responsibility" required>'
        f'{_responsibility_options()}</select></label></div>'
        '<div><label>Due date <span class="muted">optional</span><input name="due_on" type="date"></label></div>'
        f'{song_binding}'
        '<button type="submit">Remember follow-up</button>'
        '<p class="muted">Remembering a follow-up is not a message, calendar event, reminder delivery or external action.</p>'
        '</form>'
    )


def _obligation_form(shell: ConsumerShell, person: Person) -> str:
    song = shell.runtime.headquarters.store.active_song()
    song_binding = ""
    if song is not None:
        song_binding = (
            '<label><input type="checkbox" name="bind_song" value="1"> '
            f'Bind this obligation to current Song: <strong>{html.escape(song.title)}</strong></label>'
        )
    return (
        '<form class="stack" method="post" action="/people/obligation/create" '
        f'aria-label="Add obligation for {html.escape(person.display_name, quote=True)}">'
        f'{shell._hidden(_obligation_create_action(shell, person))}'
        f'<div><label>Obligation<textarea name="summary" maxlength="{_MAX_OBLIGATION_SUMMARY}" rows="3" required></textarea></label></div>'
        '<div><label>Kind<select name="kind" required>'
        f'{_obligation_kind_options()}</select></label></div>'
        '<div><label>Who is responsible?<select name="responsibility" required>'
        f'{_responsibility_options()}</select></label></div>'
        '<div><label>Due date <span class="muted">optional</span><input name="due_on" type="date"></label></div>'
        f'<div><label>Trigger <span class="muted">optional</span><input name="trigger_ref" type="text" maxlength="{_MAX_OBLIGATION_TRIGGER}" placeholder="When the final vocal is approved"></label></div>'
        f'<div><label>Consequence or dependency <span class="muted">optional</span><textarea name="consequence_note" maxlength="{_MAX_OBLIGATION_CONSEQUENCE}" rows="2"></textarea></label></div>'
        f'<div><label>What makes this true?<textarea name="source_note" maxlength="{_MAX_OBLIGATION_SOURCE_NOTE}" rows="2" placeholder="I promised this in our conversation..." required></textarea></label></div>'
        f'{song_binding}'
        '<button type="submit">Remember obligation</button>'
        '<p class="muted">This records your statement as USER_DECLARED evidence. It does not send, schedule, pay, license, verify a provider, or perform any external action.</p>'
        '</form>'
    )


def _followup_status(followup: FollowUp) -> str:
    responsibility = _RESPONSIBILITY_LABELS[followup.responsibility]
    due = "No due date" if followup.due_on is None else f"Due {followup.due_on}"
    return f"{responsibility} · {due}"


def _followup_song(shell: ConsumerShell, followup: FollowUp) -> str:
    if followup.song_id is None:
        return "Artist-wide"
    song = shell.runtime.headquarters.store.get_song(followup.song_id)
    if song is None:
        raise ConsumerShellError("A follow-up references Song state that is no longer readable")
    return f"Song: {song.title}"


def _obligation_song(shell: ConsumerShell, obligation: ObligationSnapshot) -> str:
    if obligation.song_id is None:
        return "Artist-wide"
    song = shell.runtime.headquarters.store.get_song(obligation.song_id)
    if song is None:
        raise ConsumerShellError("An obligation references Song state that is no longer readable")
    return f"Song: {song.title}"


def _followup_markup(shell: ConsumerShell, followup: FollowUp) -> str:
    resolve = _close_action(shell, followup, "people-followup-resolve")
    cancel = _close_action(shell, followup, "people-followup-cancel")
    return (
        '<li class="stack">'
        f'<p><strong>{html.escape(followup.summary)}</strong></p>'
        f'<p class="status caution">{html.escape(_followup_status(followup))}</p>'
        f'<p class="muted">{html.escape(_followup_song(shell, followup))}</p>'
        '<form class="stack" method="post" action="/people/followup/resolve">'
        f'{shell._hidden(resolve)}'
        f'<div><label>What closed the loop?<input name="resolution_note" type="text" maxlength="{_MAX_RESOLUTION_NOTE}" required></label></div>'
        '<button class="primary" type="submit">Mark resolved</button>'
        '</form>'
        '<details><summary>Cancel this follow-up</summary>'
        '<form class="stack" method="post" action="/people/followup/cancel">'
        f'{shell._hidden(cancel)}'
        f'<div><label>Why is it no longer needed?<input name="resolution_note" type="text" maxlength="{_MAX_RESOLUTION_NOTE}" required></label></div>'
        '<button type="submit">Cancel follow-up</button>'
        '</form></details>'
        '</li>'
    )


def _evidence_source_text(source_kind: str, source_ref: str | None) -> str:
    if source_ref is None:
        return f"{source_kind} · local declaration; no external source reference"
    return f"{source_kind} · source reference: {source_ref}"


def _source_note(shell: ConsumerShell, obligation: ObligationSnapshot) -> str:
    claim = shell.runtime.headquarters.evidence.get_claim(obligation.source_claim_id)
    if claim is None:
        raise ConsumerShellError("An obligation references source evidence that is no longer readable")
    if isinstance(claim.value, dict):
        note = claim.value.get("source_note")
        if isinstance(note, str) and note.strip():
            return note.strip()
    return "No human-readable source note was preserved in this declaration."


def _evidence_history_markup(shell: ConsumerShell, obligation: ObligationSnapshot) -> str:
    rows: list[str] = []
    for event in obligation.events:
        note = "Initial obligation declaration" if event.note is None else event.note
        source = _evidence_source_text(event.source_kind, event.source_ref)
        rows.append(
            '<li>'
            f'<strong>Lifecycle: {html.escape(event.status)}</strong> · '
            f'{html.escape(source)}<br><span class="muted">{html.escape(note)}</span>'
            '</li>'
        )
    for trigger in obligation.trigger_events:
        source = _evidence_source_text(trigger.source_kind, trigger.source_ref)
        rows.append(
            '<li>'
            f'<strong>Canonical trigger event</strong> · {html.escape(source)}<br>'
            f'<span class="muted">{html.escape(trigger.note)}</span>'
            '</li>'
        )
    for claim in _declared_trigger_claims(shell, obligation):
        if not isinstance(claim.value, dict):
            raise ConsumerShellError("Declared trigger evidence lost its human-readable note")
        note = claim.value.get("note")
        if not isinstance(note, str) or not note.strip():
            raise ConsumerShellError("Declared trigger evidence lost its human-readable note")
        source = _evidence_source_text(claim.source_kind, claim.source_ref)
        rows.append(
            '<li>'
            f'<strong>Declared trigger evidence</strong> · {html.escape(source)}<br>'
            f'<span class="muted">{html.escape(note.strip())}</span>'
            '</li>'
        )
    return '<ol class="stack">' + "".join(rows) + "</ol>"


def _allowed_transition_statuses(obligation: ObligationSnapshot) -> tuple[str, ...]:
    if obligation.terminal:
        return ()
    if obligation.status == "OPEN":
        return ("BLOCKED", "DISPUTED", "SATISFIED", "WAIVED", "CANCELED")
    if obligation.status == "BLOCKED":
        return ("OPEN", "DISPUTED", "SATISFIED", "WAIVED", "CANCELED")
    if obligation.status == "DISPUTED":
        return ("OPEN", "BLOCKED", "SATISFIED", "WAIVED", "CANCELED")
    return tuple(status for status in OBLIGATION_STATUSES if status != obligation.status)


def _transition_forms(shell: ConsumerShell, obligation: ObligationSnapshot) -> str:
    forms: list[str] = []
    for target in _allowed_transition_statuses(obligation):
        action = _obligation_transition_action(shell, obligation, target)
        forms.append(
            '<form class="stack" method="post" action="/people/obligation/transition">'
            f'{shell._hidden(action)}'
            f'<input type="hidden" name="status" value="{html.escape(target, quote=True)}">'
            f'<div><label>Why {_OBLIGATION_STATUS_LABELS[target].lower()}?<input name="judgment_note" type="text" maxlength="{_MAX_OBLIGATION_JUDGMENT_NOTE}" required></label></div>'
            f'<button type="submit">Mark {_OBLIGATION_STATUS_LABELS[target].lower()}</button>'
            '</form>'
        )
    if not forms:
        return '<p class="muted">Terminal lifecycle state. Historical evidence remains readable.</p>'
    return "".join(forms)


def _trigger_form(shell: ConsumerShell, obligation: ObligationSnapshot) -> str:
    if obligation.trigger_ref is None or obligation.terminal or obligation.trigger_events:
        return ""
    return (
        '<details><summary>Record trigger evidence</summary>'
        '<form class="stack" method="post" action="/people/obligation/trigger">'
        f'{shell._hidden(_obligation_trigger_action(shell, obligation))}'
        f'<div><label>What are you declaring about this trigger?<textarea name="trigger_note" maxlength="{_MAX_OBLIGATION_JUDGMENT_NOTE}" rows="2" required></textarea></label></div>'
        '<button type="submit">Record declared trigger evidence</button>'
        '<p class="muted">This records USER_DECLARED evidence only. It does not satisfy the trigger or silently become observed, measured, or provider-verified truth.</p>'
        '</form></details>'
    )


def _obligation_markup(shell: ConsumerShell, obligation: ObligationSnapshot) -> str:
    as_of = date.today().isoformat()
    timing = obligation.due_state(as_of=as_of)
    attention = obligation.attention_state(as_of=as_of)
    due = "No due date" if obligation.due_on is None else f"Due {obligation.due_on}"
    declared_triggers = _declared_trigger_claims(shell, obligation)
    if obligation.trigger_ref is None:
        trigger = "No trigger"
    elif obligation.trigger_events:
        trigger = f"Canonical trigger evidence recorded: {obligation.trigger_ref}"
    elif declared_triggers:
        trigger = (
            f"Declared trigger evidence recorded: {obligation.trigger_ref} · "
            "trigger remains pending until legitimate observed, measured, or provider-verified evidence is recorded"
        )
    else:
        trigger = f"Waiting for trigger: {obligation.trigger_ref}"
    consequence = (
        "No consequence or dependency recorded"
        if obligation.consequence_note is None
        else f"Consequence/dependency: {obligation.consequence_note}"
    )
    source = _evidence_source_text(obligation.source_kind, obligation.source_ref)
    source_note = _source_note(shell, obligation)
    return (
        '<li class="stack">'
        f'<p><strong>{html.escape(obligation.summary)}</strong></p>'
        f'<p class="status caution">Status: {html.escape(obligation.status)} · Attention: {html.escape(attention)}</p>'
        f'<p>{html.escape(_OBLIGATION_KIND_LABELS[obligation.kind])} · {html.escape(_RESPONSIBILITY_LABELS[obligation.responsibility])}</p>'
        f'<p class="muted">Person: {html.escape(obligation.person_display_name)} · {html.escape(_obligation_song(shell, obligation))}</p>'
        f'<p>Timing: <strong>{html.escape(timing)}</strong> · {html.escape(due)}</p>'
        f'<p>{html.escape(trigger)}</p>'
        f'<p>{html.escape(consequence)}</p>'
        f'<p><strong>Source note:</strong> {html.escape(source_note)}</p>'
        f'<p class="muted">Provenance: {html.escape(source)} · source current: {str(obligation.source_current).lower()}</p>'
        '<details><summary>Evidence history</summary>'
        f'{_evidence_history_markup(shell, obligation)}'
        '</details>'
        f'{_trigger_form(shell, obligation)}'
        '<details><summary>Record lifecycle judgment</summary>'
        f'{_transition_forms(shell, obligation)}'
        '<p class="muted">Lifecycle judgments are local USER_DECLARED evidence. They do not send, pay, license, schedule, publish, or perform anything externally.</p>'
        '</details>'
        '</li>'
    )


def _person_markup(shell: ConsumerShell, person: Person) -> str:
    followups = shell.runtime.headquarters.people.open_followups(person_id=person.id)
    obligations = shell.runtime.headquarters.obligations.for_person(person.id)
    context = (
        ""
        if person.relationship_context is None
        else f'<p>{html.escape(person.relationship_context)}</p>'
    )
    if followups:
        rows = "".join(_followup_markup(shell, item) for item in followups)
        open_html = (
            f'<h3>Open loops</h3><ol class="stack" aria-label="Open follow-ups for {html.escape(person.display_name, quote=True)}">{rows}</ol>'
        )
    else:
        open_html = '<p class="status good">No open follow-ups</p>'
    if obligations:
        obligation_rows = "".join(
            _obligation_markup(shell, item) for item in reversed(obligations)
        )
        obligation_html = (
            f'<h3>Obligations</h3><ol class="stack" aria-label="Obligations for {html.escape(person.display_name, quote=True)}">{obligation_rows}</ol>'
        )
    else:
        obligation_html = '<p class="muted">No obligations recorded for this person.</p>'
    return (
        '<div class="card stack">'
        f'<h2>{html.escape(person.display_name)}</h2>'
        f'{context}{open_html}{obligation_html}'
        '<details><summary>Add a follow-up</summary>'
        f'{_followup_form(shell, person)}'
        '</details>'
        '<details><summary>Add an obligation</summary>'
        f'{_obligation_form(shell, person)}'
        '</details></div>'
    )


def _people_content(shell: ConsumerShell) -> str:
    if shell.runtime.state != "RUNNING":
        raise ConsumerShellError("People requires an open Artist workspace")
    people = shell.runtime.headquarters.people.people()
    people_html = (
        '<div class="card"><h2>Your people</h2><p class="muted">No people recorded yet. Add only the relationship context you actually know.</p></div>'
        if not people
        else "".join(_person_markup(shell, person) for person in people)
    )
    return f'<section class="grid">{_person_form(shell)}{people_html}</section>'


def _post_create_person(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    action = shell._consume_action(form.get("action", ""), "people-create")
    if action is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That add-person action was already handled or expired."),
        )
        return
    name = _clean_human_text(
        form.get("display_name", ""),
        "Person name",
        maximum=_MAX_PERSON_NAME,
    )
    context = _optional_human_text(
        form.get("relationship_context"),
        "Relationship context",
        maximum=_MAX_RELATIONSHIP_CONTEXT,
    )
    person = shell.runtime.headquarters.people.create_person(
        name,
        relationship_context=context,
    )
    shell._consumer_notice = f"{person.display_name} added to your local People context. Nothing was sent or merged."
    shell._redirect(handler, "/people")


def _parse_followup_binding(value: str) -> tuple[str, str | None]:
    person_id, separator, song_marker = value.partition("|")
    if not separator or not person_id or not song_marker:
        raise ConsumerShellError("That follow-up action is no longer valid")
    return person_id, None if song_marker == "~" else song_marker


def _post_create_followup(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    action = shell._consume_action(form.get("action", ""), "people-followup-create")
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That follow-up action was already handled or expired."),
        )
        return
    person_id, rendered_song_id = _parse_followup_binding(action.value)
    bind_song = form.get("bind_song")
    if bind_song not in {None, "1"}:
        raise ConsumerShellError("That Song binding choice is invalid")
    song_id: str | None = None
    if bind_song == "1":
        active = shell.runtime.headquarters.store.active_song()
        if active is None or rendered_song_id is None or active.id != rendered_song_id:
            raise ConsumerShellError(
                "The active Song changed. Reload People before binding this follow-up to a Song."
            )
        song_id = active.id
    responsibility = str(form.get("responsibility", "")).strip().upper()
    if responsibility not in FOLLOWUP_RESPONSIBILITIES:
        raise ConsumerShellError("Choose who has the next move")
    summary = _clean_human_text(
        form.get("summary", ""),
        "Follow-up",
        maximum=_MAX_FOLLOWUP_SUMMARY,
    )
    due_on = _optional_human_text(form.get("due_on"), "Due date", maximum=10)
    shell.runtime.headquarters.people.create_followup(
        person_id,
        summary,
        responsibility=responsibility,
        song_id=song_id,
        due_on=due_on,
    )
    shell._consumer_notice = "Follow-up remembered locally. N0TE did not message anyone or create an external reminder."
    shell._redirect(handler, "/people")


def _post_close_followup(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
    *,
    action_kind: str,
    close_state: str,
) -> None:
    action = shell._consume_action(form.get("action", ""), action_kind)
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That follow-up close action was already handled or expired."),
        )
        return
    note = _clean_human_text(
        form.get("resolution_note", ""),
        "Closure note",
        maximum=_MAX_RESOLUTION_NOTE,
    )
    if close_state == "RESOLVED":
        shell.runtime.headquarters.people.resolve_followup(
            action.value,
            resolution_note=note,
        )
        shell._consumer_notice = "Follow-up resolved and preserved in local history."
    else:
        shell.runtime.headquarters.people.cancel_followup(action.value, reason=note)
        shell._consumer_notice = "Follow-up canceled and preserved in local history."
    shell._redirect(handler, "/people")


def _record_declared_evidence(
    shell: ConsumerShell,
    *,
    song_id: str | None,
    key: str,
    value: object,
):
    hq = shell.runtime.headquarters
    return hq.evidence.record_claim(
        scope_kind="SONG" if song_id is not None else "ARTIST",
        scope_id=song_id if song_id is not None else hq.store.primary_artist_id,
        key=key,
        value=value,
        source_kind="USER_DECLARED",
        source_ref=None,
        confidence=1.0,
        twin_domain="UNSPECIFIED",
    )


def _post_create_obligation(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    action = shell._consume_action(form.get("action", ""), "people-obligation-create")
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That obligation action was already handled or expired."),
        )
        return
    person_id, rendered_song_id = _parse_followup_binding(action.value)
    person = shell.runtime.headquarters.people.get_person(person_id)
    if person is None:
        raise ConsumerShellError("That person is no longer available")
    bind_song = form.get("bind_song")
    if bind_song not in {None, "1"}:
        raise ConsumerShellError("That Song binding choice is invalid")
    song_id: str | None = None
    if bind_song == "1":
        active = shell.runtime.headquarters.store.active_song()
        if active is None or rendered_song_id is None or active.id != rendered_song_id:
            raise ConsumerShellError(
                "The active Song changed. Reload People before binding this obligation to a Song."
            )
        song_id = active.id
    kind = str(form.get("kind", "")).strip().upper()
    if kind not in OBLIGATION_KINDS:
        raise ConsumerShellError("Choose a supported obligation kind")
    responsibility = str(form.get("responsibility", "")).strip().upper()
    if responsibility not in FOLLOWUP_RESPONSIBILITIES:
        raise ConsumerShellError("Choose who is responsible for this obligation")
    summary = _clean_human_text(
        form.get("summary", ""), "Obligation", maximum=_MAX_OBLIGATION_SUMMARY
    )
    due_on = _optional_human_text(form.get("due_on"), "Due date", maximum=10)
    trigger_ref = _optional_human_text(
        form.get("trigger_ref"), "Trigger", maximum=_MAX_OBLIGATION_TRIGGER
    )
    consequence_note = _optional_human_text(
        form.get("consequence_note"),
        "Consequence or dependency",
        maximum=_MAX_OBLIGATION_CONSEQUENCE,
    )
    source_note = _clean_human_text(
        form.get("source_note", ""),
        "Source note",
        maximum=_MAX_OBLIGATION_SOURCE_NOTE,
    )
    claim = _record_declared_evidence(
        shell,
        song_id=song_id,
        key=f"obligation.declaration.{uuid.uuid4().hex}",
        value={
            "statement": summary,
            "source_note": source_note,
            "person_id": person.id,
            "kind": kind,
            "responsibility": responsibility,
            "due_on": due_on,
            "trigger_ref": trigger_ref,
            "consequence_note": consequence_note,
        },
    )
    shell.runtime.headquarters.obligations.create_obligation(
        person.id,
        kind=kind,
        responsibility=responsibility,
        summary=summary,
        source_claim_id=claim.id,
        song_id=song_id,
        due_on=due_on,
        trigger_ref=trigger_ref,
        consequence_note=consequence_note,
    )
    shell._consumer_notice = (
        "Obligation remembered from explicit USER_DECLARED evidence. "
        "N0TE did not verify a provider or perform an external action."
    )
    shell._redirect(handler, "/people")


def _parse_lifecycle_binding(value: str) -> tuple[str, int, str]:
    parts = value.split("|")
    if len(parts) != 3 or not all(parts):
        raise ConsumerShellError("That obligation lifecycle action is no longer valid")
    try:
        sequence = int(parts[1])
    except ValueError as exc:
        raise ConsumerShellError("That obligation lifecycle action is no longer valid") from exc
    return parts[0], sequence, parts[2]


def _post_transition_obligation(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    status = str(form.get("status", "")).strip().upper()
    if status not in OBLIGATION_STATUSES:
        raise ConsumerShellError("Choose a supported obligation lifecycle judgment")
    action = shell._consume_action(
        form.get("action", ""), f"people-obligation-{status.lower()}"
    )
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That obligation lifecycle action was already handled or expired."),
        )
        return
    obligation_id, rendered_event_sequence, rendered_status = _parse_lifecycle_binding(
        action.value
    )
    current = shell.runtime.headquarters.obligations.get(obligation_id)
    if (
        _latest_event_sequence(current) != rendered_event_sequence
        or current.status != rendered_status
    ):
        shell._send_html(
            handler,
            409,
            shell._simple_error(
                "That obligation changed since this page was rendered. Reload People before recording another judgment."
            ),
        )
        return
    if status not in _allowed_transition_statuses(current):
        raise ConsumerShellError("That lifecycle transition is no longer available")
    note = _clean_human_text(
        form.get("judgment_note", ""),
        "Lifecycle judgment note",
        maximum=_MAX_OBLIGATION_JUDGMENT_NOTE,
    )
    claim = _record_declared_evidence(
        shell,
        song_id=current.song_id,
        key=f"obligation.lifecycle.{current.id}.{uuid.uuid4().hex}",
        value={
            "obligation": current.summary,
            "from_status": current.status,
            "status": status,
            "note": note,
        },
    )
    shell.runtime.headquarters.obligations.transition(
        current.id,
        status=status,
        evidence_claim_id=claim.id,
        note=note,
    )
    shell._consumer_notice = (
        f"Obligation marked {_OBLIGATION_STATUS_LABELS[status].lower()} from USER_DECLARED evidence. "
        "No external action was performed."
    )
    shell._redirect(handler, "/people")


def _parse_trigger_binding(value: str) -> tuple[str, int, str, str, str]:
    parts = value.split("|")
    if len(parts) != 5 or not all(parts):
        raise ConsumerShellError("That trigger-evidence action is no longer valid")
    try:
        event_sequence = int(parts[1])
    except ValueError as exc:
        raise ConsumerShellError("That trigger-evidence action is no longer valid") from exc
    return parts[0], event_sequence, parts[2], parts[3], parts[4]


def _post_record_trigger(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    action = shell._consume_action(form.get("action", ""), "people-obligation-trigger")
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That trigger-evidence action was already handled or expired."),
        )
        return
    (
        obligation_id,
        rendered_event_sequence,
        rendered_status,
        rendered_trigger_sequence,
        rendered_declared_trigger_sequence,
    ) = _parse_trigger_binding(action.value)
    current = shell.runtime.headquarters.obligations.get(obligation_id)
    if (
        _latest_event_sequence(current) != rendered_event_sequence
        or current.status != rendered_status
        or _latest_trigger_sequence(current) != rendered_trigger_sequence
        or _latest_declared_trigger_sequence(shell, current)
        != rendered_declared_trigger_sequence
    ):
        shell._send_html(
            handler,
            409,
            shell._simple_error(
                "That obligation changed since this page was rendered. Reload People before recording trigger evidence."
            ),
        )
        return
    if current.trigger_ref is None or current.terminal or current.trigger_events:
        raise ConsumerShellError("That trigger-evidence action is no longer available")
    note = _clean_human_text(
        form.get("trigger_note", ""),
        "Trigger evidence note",
        maximum=_MAX_OBLIGATION_JUDGMENT_NOTE,
    )
    _record_declared_evidence(
        shell,
        song_id=current.song_id,
        key=_declared_trigger_key(current),
        value={
            "obligation_id": current.id,
            "obligation": current.summary,
            "trigger_ref": current.trigger_ref,
            "truth_class": "DECLARED",
            "note": note,
        },
    )
    shell._consumer_notice = (
        "Declared trigger evidence recorded. The trigger remains pending until legitimate "
        "observed, measured, or provider-verified evidence is recorded."
    )
    shell._redirect(handler, "/people")


def install_people_headquarters() -> None:
    """Attach the local People/Follow-up/Obligation Headquarters surface exactly once."""
    if getattr(ConsumerShell, "_people_headquarters_installed", False):
        return

    consumer_shell_module._NAV_ROUTES["/people"] = "People"
    original_running_state: Callable[[ConsumerShell, str], _PageState] = ConsumerShell._running_state
    original_state_content: Callable[[ConsumerShell, _PageState], str] = ConsumerShell._state_content
    original_post: Callable[[ConsumerShell, BaseHTTPRequestHandler], None] = ConsumerShell._handle_post

    def with_people_state(self: ConsumerShell, path: str) -> _PageState:
        if path != "/people":
            return original_running_state(self, path)
        artist = self.runtime.headquarters.store.artist()
        song = self.runtime.headquarters.store.active_song()
        return _PageState(
            "running-people",
            "People and open loops",
            "Artist Headquarters",
            "Remember who matters, what was promised, what you are waiting on, and what needs a follow-up without turning local context into an external action.",
            artist_name=artist.display_name,
            song_title=None if song is None else song.title,
        )

    def with_people_content(self: ConsumerShell, state: _PageState) -> str:
        if state.kind == "running-people":
            return _people_content(self)
        return original_state_content(self, state)

    def with_people_post(self: ConsumerShell, handler: BaseHTTPRequestHandler) -> None:
        path = self._path(handler)
        if path not in {
            "/people/create",
            "/people/followup/create",
            "/people/followup/resolve",
            "/people/followup/cancel",
            "/people/obligation/create",
            "/people/obligation/transition",
            "/people/obligation/trigger",
        }:
            original_post(self, handler)
            return
        if not self._request_host_is_exact(handler) or not self._post_origin_is_allowed(handler):
            self._send_html(
                handler,
                403,
                self._simple_error("That action did not come from this N0TE window."),
            )
            return
        if self.runtime.state != "RUNNING":
            self._send_html(
                handler,
                409,
                self._simple_error("Open an Artist workspace before changing People context."),
            )
            return
        form = self._read_form(handler)
        if form is None or not self._form_authorized(form):
            self._send_html(
                handler,
                403,
                self._simple_error("That action expired. Reload N0TE and try again."),
            )
            return
        try:
            if path == "/people/create":
                _post_create_person(self, handler, form)
            elif path == "/people/followup/create":
                _post_create_followup(self, handler, form)
            elif path == "/people/followup/resolve":
                _post_close_followup(
                    self,
                    handler,
                    form,
                    action_kind="people-followup-resolve",
                    close_state="RESOLVED",
                )
            elif path == "/people/followup/cancel":
                _post_close_followup(
                    self,
                    handler,
                    form,
                    action_kind="people-followup-cancel",
                    close_state="CANCELED",
                )
            elif path == "/people/obligation/create":
                _post_create_obligation(self, handler, form)
            elif path == "/people/obligation/transition":
                _post_transition_obligation(self, handler, form)
            else:
                _post_record_trigger(self, handler, form)
        except (NotFoundError, ValidationError, ConsumerShellError) as exc:
            self._consumer_notice = str(exc)
            self._redirect(handler, "/people")
        except Exception:
            self._send_html(
                handler,
                500,
                self._simple_error(
                    "N0TE stopped that People action before it could become unclear relationship state."
                ),
            )

    ConsumerShell._running_state = with_people_state
    ConsumerShell._state_content = with_people_content
    ConsumerShell._handle_post = with_people_post
    ConsumerShell._people_headquarters_installed = True
