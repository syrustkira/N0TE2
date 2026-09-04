from __future__ import annotations

import html
from typing import Callable

from .consumer_shell import ConsumerShell, _FOCUS_SONG_DEFAULT_MODES

_JOB_ORDER = ("MAKE", "FINISH", "MANAGE", "RELEASE", "PERFORM")
_JOB_COPY = {
    "MAKE": "Create, explore, or get the musical idea moving.",
    "FINISH": "Protect the decisions that move the current work toward done.",
    "MANAGE": "Plan and organize Artist Headquarters work without dragging setup into the session.",
    "RELEASE": "Work on release readiness first; connect services only when a real release step requires them.",
    "PERFORM": "Protect rehearsal and performance preparation without turning on unrelated studio plumbing.",
}


def _job_entry_section(shell: ConsumerShell, state) -> str:  # noqa: ANN001
    focus = shell.runtime.headquarters.attention.active_focus()
    active_song = shell.runtime.headquarters.store.active_song()
    active_song_id = None if active_song is None else active_song.id
    forms: list[str] = []
    for mode in _JOB_ORDER:
        token = shell._new_action("focus-set", mode)
        expected_song_id = (
            active_song_id if mode in _FOCUS_SONG_DEFAULT_MODES else None
        )
        current = (
            focus is not None
            and focus.mode == mode
            and focus.song_id == expected_song_id
        )
        button_class = "primary" if current else ""
        pressed = "true" if current else "false"
        forms.append(
            '<div class="card stack">'
            f'<h2>{html.escape(mode.title())}</h2>'
            f'<p>{html.escape(_JOB_COPY[mode])}</p>'
            f'<form method="post" action="/focus/set">{shell._hidden(token)}'
            f'<button class="{button_class}" type="submit" aria-pressed="{pressed}">'
            f'{html.escape("Continue" if current else "Choose " + mode.title())}</button>'
            '</form></div>'
        )

    if state.song_title:
        binding = (
            f'Make and Finish follow <strong>{html.escape(state.song_title)}</strong>. '
            'Manage, Release and Perform stay Artist-wide until a later journey supplies a more exact context.'
        )
    else:
        binding = (
            'You can choose any Artist-wide job now. Start or select a Song before choosing Make or Finish if you want that Focus bound to the Song.'
        )

    return (
        '<section class="stack" aria-labelledby="job-entry-heading">'
        '<div class="card">'
        '<h2 id="job-entry-heading">What are you here to do?</h2>'
        '<p>Choose the artist job first. N0TE keeps setup progressive and contextual instead of making you configure tools before you can work.</p>'
        f'<p>{binding}</p>'
        '<p class="status good">Choosing a job changes Focus only</p>'
        '<p class="muted">No DAW, AI provider, account, service, send, publish, purchase, or external write is required or authorized by this choice.</p>'
        '</div>'
        f'<div class="grid">{"".join(forms)}</div>'
        '</section>'
    )


def install_progressive_job_entry() -> None:
    """Attach artist-first job selection to Home without owning Focus semantics."""
    if getattr(ConsumerShell, "_progressive_job_entry_installed", False):
        return

    original_state_content: Callable[[ConsumerShell, object], str] = (
        ConsumerShell._state_content
    )

    def with_progressive_job_entry(self: ConsumerShell, state) -> str:  # noqa: ANN001
        rendered = original_state_content(self, state)
        if state.kind not in {"running-no-song", "running-home"}:
            return rendered
        return rendered + _job_entry_section(self, state)

    ConsumerShell._state_content = with_progressive_job_entry
    ConsumerShell._progressive_job_entry_installed = True
