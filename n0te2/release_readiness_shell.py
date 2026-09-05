from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler
from typing import Callable, Mapping

from . import consumer_shell as consumer_shell_module
from .consumer_shell import ConsumerShell, ConsumerShellError, _PageState
from .lineage import LineageCorruptionError, NotFoundError, ValidationError
from .release_readiness import (
    DELIVERABLE_KINDS,
    DELIVERABLE_STATES,
    MILESTONE_STATES,
    DeliverableBinding,
    MilestoneBinding,
    PlanBinding,
    ReleaseReadinessError,
    ReleaseReadinessMemory,
    StaleReleasePlanError,
)

_MAX_LABEL = 240
_MAX_NOTE = 1200
_MAX_ARCHIVE_NOTE = 1000
_KIND_LABELS = {
    "MASTER_FILE": "Master file",
    "COVER_ART": "Cover artwork",
    "METADATA": "Release metadata",
    "RIGHTS_CREDITS": "Rights / credits review",
    "CAMPAIGN_ASSET": "Campaign asset",
    "PITCH_ASSET": "Pitch asset",
    "DIRECT_FAN_ASSET": "Direct-fan asset",
    "OTHER": "Other",
}
_STATE_LABELS = {
    "UNKNOWN": "Unknown",
    "MISSING": "Missing",
    "READY": "Ready locally",
    "BLOCKED": "Blocked",
    "NOT_REQUIRED": "Not required",
}
_MILESTONE_LABELS = {
    "OPEN": "Open",
    "DONE": "Done locally",
    "BLOCKED": "Blocked",
    "NOT_REQUIRED": "Not required",
}
_REVIEW_LABELS = {
    "BLOCKED": "Blocked",
    "MISSING": "Missing prerequisite or deliverable",
    "UNKNOWN": "Not enough local readiness truth yet",
    "IN_PROGRESS": "Release prep in progress",
    "READY_FOR_REVIEW": "Ready for release review",
}


def _memory(shell: ConsumerShell) -> ReleaseReadinessMemory:
    if shell.runtime.state != "RUNNING":
        raise ConsumerShellError("Release planning requires an open Artist workspace")
    return ReleaseReadinessMemory(shell.runtime.headquarters.store)


def _clean(value: str | None, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ConsumerShellError(f"{field} must be text")
    text = " ".join(value.split())
    if not text:
        raise ConsumerShellError(f"{field} must not be empty")
    if len(text) > maximum:
        raise ConsumerShellError(f"{field} is too long")
    return text


def _optional(value: str | None, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConsumerShellError(f"{field} must be text")
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) > maximum:
        raise ConsumerShellError(f"{field} is too long")
    return text


def _lead_days(value: str | None) -> int:
    if not isinstance(value, str):
        raise ConsumerShellError("Lead days must be a whole number")
    text = value.strip()
    if not text or not text.isascii() or not text.isdigit():
        raise ConsumerShellError("Lead days must be a whole number from 0 to 730")
    days = int(text)
    if days < 0 or days > 730:
        raise ConsumerShellError("Lead days must be a whole number from 0 to 730")
    return days


def _select(name: str, allowed: tuple[str, ...], labels: Mapping[str, str]) -> str:
    options = "".join(
        f'<option value="{html.escape(value, quote=True)}">{html.escape(labels[value])}</option>'
        for value in allowed
    )
    return f'<select name="{html.escape(name, quote=True)}" required>{options}</select>'


def _plan_value(binding: PlanBinding) -> str:
    return "|".join(
        (
            binding.plan_id,
            binding.song_id,
            binding.expected_state,
            binding.expected_revision,
        )
    )


def _parse_plan_value(value: str) -> PlanBinding:
    parts = value.split("|")
    if len(parts) != 4 or any(not part for part in parts):
        raise ConsumerShellError("That release-plan action is no longer valid")
    return PlanBinding(
        plan_id=parts[0],
        song_id=parts[1],
        expected_state=parts[2],
        expected_revision=parts[3],
    )


def _deliverable_value(
    binding: DeliverableBinding,
    *,
    song_id: str,
    target: str,
) -> str:
    return "|".join(
        (
            binding.deliverable_id,
            binding.plan_id,
            binding.expected_plan_revision,
            str(binding.expected_state_sequence),
            binding.expected_state,
            song_id,
            target,
        )
    )


def _parse_deliverable_value(value: str) -> tuple[DeliverableBinding, str, str]:
    parts = value.split("|")
    if len(parts) != 7 or any(not part for part in parts):
        raise ConsumerShellError("That deliverable action is no longer valid")
    try:
        state_sequence = int(parts[3])
    except ValueError as exc:
        raise ConsumerShellError("That deliverable action is no longer valid") from exc
    return (
        DeliverableBinding(
            deliverable_id=parts[0],
            plan_id=parts[1],
            expected_plan_revision=parts[2],
            expected_state_sequence=state_sequence,
            expected_state=parts[4],
        ),
        parts[5],
        parts[6],
    )


def _milestone_value(
    binding: MilestoneBinding,
    *,
    song_id: str,
    target: str,
) -> str:
    return "|".join(
        (
            binding.milestone_id,
            binding.plan_id,
            binding.expected_plan_revision,
            str(binding.expected_state_sequence),
            binding.expected_state,
            song_id,
            target,
        )
    )


def _parse_milestone_value(value: str) -> tuple[MilestoneBinding, str, str]:
    parts = value.split("|")
    if len(parts) != 7 or any(not part for part in parts):
        raise ConsumerShellError("That milestone action is no longer valid")
    try:
        state_sequence = int(parts[3])
    except ValueError as exc:
        raise ConsumerShellError("That milestone action is no longer valid") from exc
    return (
        MilestoneBinding(
            milestone_id=parts[0],
            plan_id=parts[1],
            expected_plan_revision=parts[2],
            expected_state_sequence=state_sequence,
            expected_state=parts[4],
        ),
        parts[5],
        parts[6],
    )


def _require_active_song(shell: ConsumerShell, expected_song_id: str):
    song = shell.runtime.headquarters.store.active_song()
    if song is None or song.id != expected_song_id:
        raise ConsumerShellError(
            "The active Song changed. Reload Release before changing this plan."
        )
    return song


def _create_plan_form(shell: ConsumerShell, song_id: str) -> str:
    action = shell._new_action("release-create-plan", song_id)
    return (
        '<form class="stack" method="post" action="/release/plan">'
        + shell._hidden(action)
        + '<label>Artist target date<input name="target_on" type="date" required></label>'
        + '<button class="primary" type="submit">Create local release plan</button>'
        + '<p class="muted">This is your planning target, not a provider-confirmed or scheduled release date.</p>'
        + '</form>'
    )


def _history(memory: ReleaseReadinessMemory, song_id: str) -> str:
    archived = [plan for plan in memory.plan_history(song_id) if plan.state == "ARCHIVED"]
    if not archived:
        return '<p class="muted">No prior release targets are archived for this Song.</p>'
    rows = "".join(
        '<li><strong>'
        + html.escape(plan.target_on)
        + '</strong><br><span class="muted">Archived: '
        + html.escape(plan.archived_note or "No archive note")
        + '</span></li>'
        for plan in reversed(archived)
    )
    return (
        '<ol class="stack" aria-label="Archived release plan history">'
        + rows
        + '</ol><p class="muted">Archived targets remain history. N0TE does not recompute them from today’s Version or checklist state.</p>'
    )


def _deliverable_state_forms(
    shell: ConsumerShell,
    memory: ReleaseReadinessMemory,
    item,
) -> str:
    binding = memory.deliverable_binding(item.id)
    targets = [state for state in DELIVERABLE_STATES if state != item.state]
    if item.required:
        targets = [state for state in targets if state != "NOT_REQUIRED"]
    forms: list[str] = []
    for target in targets:
        token = shell._new_action(
            "release-deliverable-state",
            _deliverable_value(binding, song_id=item.song_id, target=target),
        )
        forms.append(
            '<form class="stack" method="post" action="/release/deliverable/state">'
            + shell._hidden(token)
            + f'<label>Context for “{html.escape(_STATE_LABELS[target])}” <span class="muted">optional</span><input name="note" type="text" maxlength="{_MAX_NOTE}" autocomplete="off"></label>'
            + '<button type="submit">Mark '
            + html.escape(_STATE_LABELS[target].lower())
            + '</button></form>'
        )
    return '<div class="row">' + "".join(forms) + '</div>'


def _deliverables(shell: ConsumerShell, memory: ReleaseReadinessMemory, plan) -> str:
    items = memory.deliverables_for_plan(plan.id)
    if not items:
        return (
            '<p class="status caution">No required deliverables are defined yet.</p>'
            '<p class="muted">An empty checklist cannot become release-review ready.</p>'
        )
    rows: list[str] = []
    for item in items:
        status_class = "good" if item.state == "READY" else "caution"
        requirement = "Required" if item.required else "Optional"
        note = "" if item.note is None else '<p class="muted">' + html.escape(item.note) + '</p>'
        rows.append(
            '<li class="stack"><p><strong>'
            + html.escape(item.label)
            + '</strong> · '
            + html.escape(_KIND_LABELS[item.kind])
            + ' · '
            + requirement
            + '</p><p class="status '
            + status_class
            + '">'
            + html.escape(_STATE_LABELS[item.state])
            + '</p>'
            + note
            + _deliverable_state_forms(shell, memory, item)
            + '</li>'
        )
    return '<ol class="stack" aria-label="Release deliverables">' + "".join(rows) + '</ol>'


def _add_deliverable_form(shell: ConsumerShell, memory: ReleaseReadinessMemory, plan) -> str:
    binding = memory.plan_binding(plan.id)
    token = shell._new_action("release-add-deliverable", _plan_value(binding))
    return (
        '<details><summary>Add release deliverable</summary>'
        '<form class="stack" method="post" action="/release/deliverable">'
        + shell._hidden(token)
        + '<label>Kind'
        + _select("kind", DELIVERABLE_KINDS, _KIND_LABELS)
        + '</label>'
        + f'<label for="release-deliverable-label">Deliverable</label><input id="release-deliverable-label" name="label" type="text" maxlength="{_MAX_LABEL}" autocomplete="off" required>'
        + '<label>Requirement<select name="required" required><option value="YES">Required</option><option value="NO">Optional</option></select></label>'
        + '<label>Current local state'
        + _select("state", DELIVERABLE_STATES, _STATE_LABELS)
        + '</label>'
        + f'<label>Context <span class="muted">optional</span><input name="note" type="text" maxlength="{_MAX_NOTE}" autocomplete="off"></label>'
        + '<button type="submit">Add deliverable</button>'
        + '<p class="muted">“Ready locally” records Artist-entered preparation only. It is not provider acceptance, delivery verification, or legal clearance.</p>'
        + '</form></details>'
    )


def _milestone_state_forms(
    shell: ConsumerShell,
    memory: ReleaseReadinessMemory,
    item,
) -> str:
    binding = memory.milestone_binding(item.id)
    forms: list[str] = []
    for target in MILESTONE_STATES:
        if target == item.state:
            continue
        token = shell._new_action(
            "release-milestone-state",
            _milestone_value(binding, song_id=item.song_id, target=target),
        )
        forms.append(
            '<form class="stack" method="post" action="/release/milestone/state">'
            + shell._hidden(token)
            + f'<label>Context for “{html.escape(_MILESTONE_LABELS[target])}” <span class="muted">optional</span><input name="note" type="text" maxlength="{_MAX_NOTE}" autocomplete="off"></label>'
            + '<button type="submit">Mark '
            + html.escape(_MILESTONE_LABELS[target].lower())
            + '</button></form>'
        )
    return '<div class="row">' + "".join(forms) + '</div>'


def _milestones(shell: ConsumerShell, memory: ReleaseReadinessMemory, plan) -> str:
    items = memory.milestones_for_plan(plan.id)
    if not items:
        return (
            '<p class="muted">No backward-plan milestones yet. N0TE will not invent lead times for you.</p>'
        )
    rows: list[str] = []
    for item in items:
        status_class = "good" if item.state == "DONE" else "caution"
        note = "" if item.note is None else '<p class="muted">' + html.escape(item.note) + '</p>'
        rows.append(
            '<li class="stack"><p><strong>'
            + html.escape(item.label)
            + '</strong></p><p>Due <strong>'
            + html.escape(item.due_on)
            + '</strong> · '
            + str(item.lead_days)
            + ' days before your '
            + html.escape(item.target_on)
            + ' target</p><p class="status '
            + status_class
            + '">'
            + html.escape(_MILESTONE_LABELS[item.state])
            + '</p>'
            + note
            + _milestone_state_forms(shell, memory, item)
            + '</li>'
        )
    return '<ol class="stack" aria-label="Release backward-plan milestones">' + "".join(rows) + '</ol>'


def _add_milestone_form(shell: ConsumerShell, memory: ReleaseReadinessMemory, plan) -> str:
    binding = memory.plan_binding(plan.id)
    token = shell._new_action("release-add-milestone", _plan_value(binding))
    return (
        '<details><summary>Add backward-plan milestone</summary>'
        '<form class="stack" method="post" action="/release/milestone">'
        + shell._hidden(token)
        + f'<label>Milestone<input name="label" type="text" maxlength="{_MAX_LABEL}" autocomplete="off" required></label>'
        + '<label>Lead days before target<input name="lead_days" type="number" min="0" max="730" step="1" inputmode="numeric" required></label>'
        + f'<label>Context <span class="muted">optional</span><input name="note" type="text" maxlength="{_MAX_NOTE}" autocomplete="off"></label>'
        + '<button type="submit">Add milestone</button>'
        + '<p class="muted">The due date is calculated only from your target date and the lead days you enter. N0TE does not invent an industry deadline.</p>'
        + '</form></details>'
    )


def _archive_form(shell: ConsumerShell, memory: ReleaseReadinessMemory, plan) -> str:
    token = shell._new_action(
        "release-archive-plan",
        _plan_value(memory.plan_binding(plan.id)),
    )
    return (
        '<details><summary>Move to a different release target</summary>'
        '<form class="stack" method="post" action="/release/archive">'
        + shell._hidden(token)
        + f'<label>Why this target is being archived<input name="note" type="text" maxlength="{_MAX_ARCHIVE_NOTE}" autocomplete="off" required></label>'
        + '<button type="submit">Archive this target</button>'
        + '<p class="muted">Archiving preserves this plan as history. You can then create a new target instead of rewriting the old date.</p>'
        + '</form></details>'
    )


def _readiness_card(shell: ConsumerShell, memory: ReleaseReadinessMemory, plan) -> str:
    snapshot = memory.snapshot(plan.id)
    if snapshot.approved_version_state == "PRESENT":
        version = shell.runtime.headquarters.store.get_version(snapshot.approved_version_id or "")
        if version is None or version.song_id != plan.song_id:
            raise ReleaseReadinessError("approved Version prerequisite became unreadable")
        approval = (
            '<p class="status good">Approved Version prerequisite is present</p>'
            '<p><strong>'
            + html.escape(version.label)
            + '</strong> is the exact approved Song Version. Approval is not delivery of a master and does not authorize release.</p>'
        )
    else:
        approval = (
            '<p class="status caution">No approved Version prerequisite yet</p>'
            '<p><a href="/song">Review and approve the exact Song Version</a>. Approval still will not upload, distribute, or release it.</p>'
        )

    review_class = "good" if snapshot.review_state == "READY_FOR_REVIEW" else "caution"
    if snapshot.unresolved:
        unresolved = (
            '<ul class="stack" aria-label="Unresolved release prep">'
            + "".join('<li>' + html.escape(item) + '</li>' for item in snapshot.unresolved)
            + '</ul>'
        )
    else:
        unresolved = '<p class="muted">No unresolved local checklist item or open milestone remains.</p>'

    return (
        '<div class="card stack"><h2>Readiness</h2>'
        '<p>Artist target: <strong>'
        + html.escape(plan.target_on)
        + '</strong></p>'
        + approval
        + '<p class="status '
        + review_class
        + '">'
        + html.escape(_REVIEW_LABELS[snapshot.review_state])
        + '</p>'
        + unresolved
        + '<p class="muted">Ready for release review is a local planning state only. It is not provider acceptance, legal clearance, a distribution upload, a scheduled release, a campaign send, a pitch submission, or release authorization.</p>'
        + '</div>'
    )


def _release_content(shell: ConsumerShell) -> str:
    if shell.runtime.state != "RUNNING":
        raise ConsumerShellError("Release requires an open Artist workspace")
    store = shell.runtime.headquarters.store
    song = store.active_song()
    intro = (
        '<div class="card stack"><h2>Release plan</h2>'
        '<p>Turn one exact Song into a reviewable local release-prep plan without turning planning into provider execution.</p>'
        '<p class="status caution">No distribution, scheduling, publishing, sending, pitching, spending, payment, or provider action is enabled here.</p>'
        '<p class="muted">UNKNOWN, MISSING, BLOCKED, READY, and NOT REQUIRED stay distinct. Rights and credits remain evidence, not legal clearance.</p></div>'
    )
    if song is None:
        return (
            '<section class="grid">'
            + intro
            + '<div class="card"><p>No active Song is selected.</p><p><a href="/song">Open or start a Song</a> before creating a release plan.</p></div></section>'
        )

    memory = _memory(shell)
    plan = memory.active_plan_for_song(song.id)
    history = (
        '<div class="card stack"><h2>Prior targets</h2>'
        + _history(memory, song.id)
        + '</div>'
    )
    if plan is None:
        create = (
            '<div class="card stack"><h2>'
            + html.escape(song.title)
            + '</h2><p>No active release target is recorded for this Song.</p>'
            + _create_plan_form(shell, song.id)
            + '</div>'
        )
        return '<section class="grid">' + intro + create + history + '</section>'

    deliverables = (
        '<div class="card stack"><h2>Deliverables</h2>'
        + _deliverables(shell, memory, plan)
        + _add_deliverable_form(shell, memory, plan)
        + '</div>'
    )
    milestones = (
        '<div class="card stack"><h2>Backward plan</h2>'
        + _milestones(shell, memory, plan)
        + _add_milestone_form(shell, memory, plan)
        + '</div>'
    )
    target = (
        '<div class="card stack"><h2>Target lineage</h2><p>Current Artist target: <strong>'
        + html.escape(plan.target_on)
        + '</strong></p>'
        + _archive_form(shell, memory, plan)
        + '</div>'
    )
    return (
        '<section class="grid">'
        + intro
        + _readiness_card(shell, memory, plan)
        + deliverables
        + milestones
        + target
        + history
        + '</section>'
    )


def _post_create_plan(shell: ConsumerShell, form: Mapping[str, str]) -> None:
    action = shell._consume_action(form.get("action", ""), "release-create-plan")
    if action is None or action.value is None:
        raise ConsumerShellError("That release-plan action was already handled or expired")
    song = _require_active_song(shell, action.value)
    memory = _memory(shell)
    memory.create_plan(song.id, target_on=_clean(form.get("target_on"), field="Target date", maximum=10))
    shell._consumer_notice = (
        "Local release target recorded. Nothing was scheduled, uploaded, published, sent, pitched, purchased, or released."
    )


def _post_add_deliverable(shell: ConsumerShell, form: Mapping[str, str]) -> None:
    action = shell._consume_action(form.get("action", ""), "release-add-deliverable")
    if action is None or action.value is None:
        raise ConsumerShellError("That deliverable action was already handled or expired")
    binding = _parse_plan_value(action.value)
    _require_active_song(shell, binding.song_id)
    required_value = _clean(form.get("required"), field="Requirement", maximum=3)
    if required_value not in {"YES", "NO"}:
        raise ConsumerShellError("Requirement must be Required or Optional")
    memory = _memory(shell)
    memory.add_deliverable(
        binding,
        kind=_clean(form.get("kind"), field="Deliverable kind", maximum=32),
        label=_clean(form.get("label"), field="Deliverable", maximum=_MAX_LABEL),
        required=required_value == "YES",
        state=_clean(form.get("state"), field="Deliverable state", maximum=32),
        note=_optional(form.get("note"), field="Deliverable context", maximum=_MAX_NOTE),
    )
    shell._consumer_notice = (
        "Release deliverable recorded as local planning truth. No provider accepted or received it."
    )


def _post_deliverable_state(shell: ConsumerShell, form: Mapping[str, str]) -> None:
    action = shell._consume_action(form.get("action", ""), "release-deliverable-state")
    if action is None or action.value is None:
        raise ConsumerShellError("That deliverable action was already handled or expired")
    binding, song_id, target = _parse_deliverable_value(action.value)
    _require_active_song(shell, song_id)
    memory = _memory(shell)
    memory.set_deliverable_state(
        binding,
        state=target,
        note=_optional(form.get("note"), field="Deliverable context", maximum=_MAX_NOTE),
    )
    shell._consumer_notice = (
        "Local deliverable state recorded. READY remains Artist-entered preparation, not provider or legal verification."
    )


def _post_add_milestone(shell: ConsumerShell, form: Mapping[str, str]) -> None:
    action = shell._consume_action(form.get("action", ""), "release-add-milestone")
    if action is None or action.value is None:
        raise ConsumerShellError("That milestone action was already handled or expired")
    binding = _parse_plan_value(action.value)
    _require_active_song(shell, binding.song_id)
    memory = _memory(shell)
    memory.add_milestone(
        binding,
        label=_clean(form.get("label"), field="Milestone", maximum=_MAX_LABEL),
        lead_days=_lead_days(form.get("lead_days")),
        state="OPEN",
        note=_optional(form.get("note"), field="Milestone context", maximum=_MAX_NOTE),
    )
    shell._consumer_notice = (
        "Backward-plan milestone recorded from your explicit lead time. N0TE did not create a provider or calendar deadline."
    )


def _post_milestone_state(shell: ConsumerShell, form: Mapping[str, str]) -> None:
    action = shell._consume_action(form.get("action", ""), "release-milestone-state")
    if action is None or action.value is None:
        raise ConsumerShellError("That milestone action was already handled or expired")
    binding, song_id, target = _parse_milestone_value(action.value)
    _require_active_song(shell, song_id)
    memory = _memory(shell)
    memory.set_milestone_state(
        binding,
        state=target,
        note=_optional(form.get("note"), field="Milestone context", maximum=_MAX_NOTE),
    )
    shell._consumer_notice = (
        "Local milestone state recorded. No external calendar, provider, campaign, or release action occurred."
    )


def _post_archive(shell: ConsumerShell, form: Mapping[str, str]) -> None:
    action = shell._consume_action(form.get("action", ""), "release-archive-plan")
    if action is None or action.value is None:
        raise ConsumerShellError("That release-target action was already handled or expired")
    binding = _parse_plan_value(action.value)
    _require_active_song(shell, binding.song_id)
    memory = _memory(shell)
    memory.archive_plan(
        binding,
        note=_clean(form.get("note"), field="Archive reason", maximum=_MAX_ARCHIVE_NOTE),
    )
    shell._consumer_notice = (
        "Prior release target archived as history. Create a new target when you are ready; the old date was not rewritten."
    )


def install_release_readiness_headquarters() -> None:
    """Attach the Song-bound local Release surface exactly once."""
    if getattr(ConsumerShell, "_release_readiness_headquarters_installed", False):
        return

    consumer_shell_module._NAV_ROUTES["/release"] = "Release"
    original_running_state: Callable[[ConsumerShell, str], _PageState] = ConsumerShell._running_state
    original_state_content: Callable[[ConsumerShell, _PageState], str] = ConsumerShell._state_content
    original_post: Callable[[ConsumerShell, BaseHTTPRequestHandler], None] = ConsumerShell._handle_post

    def with_release_state(self: ConsumerShell, path: str) -> _PageState:
        if path != "/release":
            return original_running_state(self, path)
        artist = self.runtime.headquarters.store.artist()
        song = self.runtime.headquarters.store.active_song()
        return _PageState(
            "running-release",
            "Release readiness",
            "Catalog / Release",
            "Turn the active Song into a truthful local deliverables and backward-plan review without silently converting readiness into release authority.",
            artist_name=artist.display_name,
            song_title=None if song is None else song.title,
        )

    def with_release_content(self: ConsumerShell, state: _PageState) -> str:
        if state.kind == "running-release":
            return _release_content(self)
        return original_state_content(self, state)

    def with_release_post(self: ConsumerShell, handler: BaseHTTPRequestHandler) -> None:
        path = self._path(handler)
        release_paths = {
            "/release/plan",
            "/release/deliverable",
            "/release/deliverable/state",
            "/release/milestone",
            "/release/milestone/state",
            "/release/archive",
        }
        if path not in release_paths:
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
                self._simple_error("Open an Artist workspace before changing Release planning."),
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
            if path == "/release/plan":
                _post_create_plan(self, form)
            elif path == "/release/deliverable":
                _post_add_deliverable(self, form)
            elif path == "/release/deliverable/state":
                _post_deliverable_state(self, form)
            elif path == "/release/milestone":
                _post_add_milestone(self, form)
            elif path == "/release/milestone/state":
                _post_milestone_state(self, form)
            else:
                _post_archive(self, form)
        except StaleReleasePlanError:
            self._consumer_notice = (
                "The release plan changed after this page was prepared. Reload Release before trying again."
            )
            self._redirect(handler, "/release")
            return
        except (ValidationError, NotFoundError, ConsumerShellError) as exc:
            self._consumer_notice = str(exc)
            self._redirect(handler, "/release")
            return
        except (ReleaseReadinessError, LineageCorruptionError):
            self._send_html(
                handler,
                500,
                self._simple_error(
                    "N0TE found Release planning state it could not represent safely and stopped before changing it."
                ),
            )
            return
        except Exception:
            self._send_html(
                handler,
                500,
                self._simple_error(
                    "N0TE stopped that Release action before planning could become unclear external authority."
                ),
            )
            return
        self._redirect(handler, "/release")

    ConsumerShell._running_state = with_release_state
    ConsumerShell._state_content = with_release_content
    ConsumerShell._handle_post = with_release_post
    ConsumerShell._release_readiness_headquarters_installed = True
