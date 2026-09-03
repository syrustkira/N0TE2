from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler
from typing import Callable, Mapping

from .consumer_shell import ConsumerShell, ConsumerShellError
from .creative_suggestions import (
    CREATIVE_DIMENSIONS,
    SUGGESTION_DISTANCES,
    CreativeSuggestion,
    CreativeSuggestionError,
    CreativeSuggestionService,
)
from .lineage import ValidationError

_DISTANCE_LABELS = {
    "FAMILIAR": "Familiar · small move",
    "ADJACENT": "Adjacent · change one dimension",
    "WILDCARD": "Wildcard · deliberate contrast",
}


def _service(shell: ConsumerShell) -> CreativeSuggestionService:
    hq = shell.runtime.headquarters
    return CreativeSuggestionService(hq.store, hq.sessions)


def _suggestion_action(shell: ConsumerShell, song_id: str) -> str:
    return shell._new_action("song-suggest", song_id)


def _result_markup(result: CreativeSuggestion | None) -> str:
    if result is None:
        return ""
    objective = (
        ""
        if result.session_objective is None
        else '<p class="muted">Current work objective: '
        + html.escape(result.session_objective)
        + "</p>"
    )
    return (
        '<div class="stack" aria-live="polite">'
        '<h3>One prompt to try</h3>'
        f'<p class="status good">{html.escape(result.distance.title())} · {html.escape(result.dimension.title())}</p>'
        f'<p><strong>{html.escape(result.title)}</strong></p>'
        f'<p>{html.escape(result.prompt)}</p>'
        f'<p class="muted">{html.escape(result.distance_explanation)}</p>'
        f'{objective}'
        '<p class="muted">Generated locally and deterministically. No AI provider was called, no project was changed, and this is not a claim about what your Song needs.</p>'
        '</div>'
    )


def _suggestion_card(shell: ConsumerShell) -> str:
    if shell.runtime.state != "RUNNING":
        raise ConsumerShellError("Creative suggestions require an open Artist workspace")
    song = shell.runtime.headquarters.store.active_song()
    if song is None:
        return ""

    distance_options = "".join(
        f'<option value="{mode}">{html.escape(_DISTANCE_LABELS[mode])}</option>'
        for mode in SUGGESTION_DISTANCES
    )
    locks = "".join(
        '<label><input type="checkbox" name="lock_'
        + dimension.lower()
        + '" value="1"> Keep '
        + html.escape(dimension.title())
        + " unchanged</label>"
        for dimension in CREATIVE_DIMENSIONS
    )
    result = getattr(shell, "_creative_suggestion_result", None)
    if result is not None and not isinstance(result, CreativeSuggestion):
        raise ConsumerShellError("creative suggestion shell state is invalid")

    return (
        '<div class="card"><h2>Suggest something</h2>'
        '<p>Ask N0TE for one bounded experiment around this Song. Choose how far to move and lock anything you do not want varied.</p>'
        '<form class="stack" method="post" action="/suggestion/create" aria-label="Create a Song suggestion">'
        f'{shell._hidden(_suggestion_action(shell, song.id))}'
        '<div><label>How far should the idea move?'
        f'<select name="distance" required>{distance_options}</select></label></div>'
        '<fieldset><legend>Keep these dimensions unchanged (optional)</legend>'
        f'<div class="stack">{locks}</div></fieldset>'
        '<button class="primary" type="submit">Suggest something</button>'
        '<p class="muted">The first suggestion layer is deliberately AI-off and non-personalized. “Familiar” means a smaller move around this Song, not a claim that N0TE already knows your taste.</p>'
        '</form>'
        f'{_result_markup(result)}</div>'
    )


def _locked_dimensions(form: Mapping[str, str]) -> tuple[str, ...]:
    locks = []
    for dimension in CREATIVE_DIMENSIONS:
        key = "lock_" + dimension.lower()
        value = form.get(key)
        if value is None:
            continue
        if value != "1":
            raise ValidationError(f"invalid lock value for {dimension}")
        locks.append(dimension)
    return tuple(locks)


def _post_suggestion(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    action = shell._consume_action(form.get("action", ""), "song-suggest")
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That suggestion action was already handled or expired."),
        )
        return
    song = shell.runtime.headquarters.store.active_song()
    if song is None or song.id != action.value:
        shell._send_html(
            handler,
            409,
            shell._simple_error("The active Song changed. Reload the Song before asking for an idea."),
        )
        return
    result = _service(shell).suggest(
        distance=form.get("distance", ""),
        locked_dimensions=_locked_dimensions(form),
        variation=0,
    )
    shell._creative_suggestion_result = result
    shell._consumer_notice = "Local creative prompt prepared. Nothing in the Song was changed."
    shell._redirect(handler, "/song")


def install_song_creative_suggestions() -> None:
    """Attach one pure creative-suggestion card and protected POST to /song."""
    if getattr(ConsumerShell, "_song_creative_suggestions_installed", False):
        return

    original_song: Callable[[ConsumerShell, object], str] = ConsumerShell._song_content
    original_post: Callable[[ConsumerShell, BaseHTTPRequestHandler], None] = ConsumerShell._handle_post

    def with_suggestion_card(self: ConsumerShell, state) -> str:
        rendered = original_song(self, state)
        marker = "</section>"
        if not rendered.endswith(marker):
            raise ConsumerShellError(
                "Song page structure changed before creative suggestions could attach safely"
            )
        return rendered[: -len(marker)] + _suggestion_card(self) + marker

    def with_suggestion_post(self: ConsumerShell, handler: BaseHTTPRequestHandler) -> None:
        if self._path(handler) != "/suggestion/create":
            original_post(self, handler)
            return
        if not self._request_host_is_exact(handler) or not self._post_origin_is_allowed(handler):
            self._send_html(
                handler,
                403,
                self._simple_error("That action did not come from this N0TE window."),
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
            _post_suggestion(self, handler, form)
        except (ValidationError, CreativeSuggestionError, ConsumerShellError) as exc:
            self._consumer_notice = str(exc)
            self._redirect(handler, "/song")
        except Exception:
            self._send_html(
                handler,
                500,
                self._simple_error(
                    "N0TE stopped that suggestion before it could become an unclear consumer state."
                ),
            )

    ConsumerShell._song_content = with_suggestion_card
    ConsumerShell._handle_post = with_suggestion_post
    ConsumerShell._song_creative_suggestions_installed = True
