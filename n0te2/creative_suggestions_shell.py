from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler
from typing import Callable, Mapping

from .attention_deferral import DEFERRAL_HORIZONS
from .consumer_shell import ConsumerShell, ConsumerShellError
from .creative_suggestions import (
    CREATIVE_DIMENSIONS,
    SUGGESTION_DISTANCES,
    SUGGESTION_ITEM_PREFIX,
    CreativeSuggestion,
    CreativeSuggestionError,
    CreativeSuggestionService,
    suggestion_context_anchor,
    suggestion_item_key,
    suggestion_semantic_key,
    suggestion_title,
)
from .lineage import ValidationError

_DISTANCE_LABELS = {
    "FAMILIAR": "Familiar · small move",
    "ADJACENT": "Adjacent · change one dimension",
    "WILDCARD": "Wildcard · deliberate contrast",
}
_HORIZON_LABELS = {
    "LATER_THIS_SONG": "Later this Song",
    "AFTER_RELEASE": "After release",
    "NEXT_SONG": "Next Song",
    "SOMEDAY": "Someday",
    "NEVER_SUGGEST_AGAIN": "Never suggest this again",
}


def _service(shell: ConsumerShell) -> CreativeSuggestionService:
    hq = shell.runtime.headquarters
    return CreativeSuggestionService(hq.store, hq.sessions, hq.attention_deferrals)


def _suggestion_action(shell: ConsumerShell, song_id: str) -> str:
    return shell._new_action("song-suggest", song_id)


def _defer_action(shell: ConsumerShell, result: CreativeSuggestion) -> str:
    return shell._new_action(
        "suggestion-defer",
        f"{result.song_id}|{result.session_id or ''}|{result.semantic_key}",
    )


def _restore_action(shell: ConsumerShell, item_key: str) -> str:
    return shell._new_action("suggestion-restore", item_key)


def _result_markup(shell: ConsumerShell, result: CreativeSuggestion | None) -> str:
    if result is None:
        return ""
    objective = (
        ""
        if result.session_objective is None
        else '<p class="muted">Current work objective: '
        + html.escape(result.session_objective)
        + "</p>"
    )
    horizon_options = "".join(
        f'<option value="{horizon}">{html.escape(_HORIZON_LABELS[horizon])}</option>'
        for horizon in DEFERRAL_HORIZONS
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
        '<form class="stack" method="post" action="/suggestion/defer" aria-label="Defer this suggestion">'
        f'{shell._hidden(_defer_action(shell, result))}'
        '<div><label>Not Now until '
        f'<select name="horizon" required>{horizon_options}</select></label></div>'
        '<button type="submit">Not Now</button>'
        '<p class="muted">This changes Attention memory, not your Song. “After release” stays hidden until N0TE has explicit Song-release evidence; it will not guess.</p>'
        '</form>'
        '</div>'
    )


def _deferred_markup(shell: ConsumerShell) -> str:
    hq = shell.runtime.headquarters
    items = hq.attention_deferrals.active_items(prefix=SUGGESTION_ITEM_PREFIX)
    if not items:
        return ""
    rows = []
    for item in items:
        semantic_key = suggestion_semantic_key(item.item_key)
        if semantic_key is None:
            raise ConsumerShellError("deferred suggestion identity is not recognized")
        rows.append(
            '<div class="stack">'
            f'<p><strong>{html.escape(suggestion_title(semantic_key))}</strong> · '
            f'{html.escape(_HORIZON_LABELS[item.horizon])}</p>'
            '<form method="post" action="/suggestion/restore">'
            f'{shell._hidden(_restore_action(shell, item.item_key))}'
            '<button type="submit">Bring it back</button>'
            '</form>'
            '</div>'
        )
    return (
        '<div class="stack"><h3>Deferred suggestions</h3>'
        '<p class="muted">These are remembered Attention choices, not forgotten dismissals. You can restore any one of them.</p>'
        + "".join(rows)
        + "</div>"
    )


def _suggestion_card(shell: ConsumerShell) -> str:
    if shell.runtime.state != "RUNNING":
        raise ConsumerShellError("Creative suggestions require an open Artist workspace")
    hq = shell.runtime.headquarters
    song = hq.store.active_song()
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
    if result is not None:
        latest = hq.sessions.latest_for_song(song.id)
        latest_session_id = None if latest is None else latest.id
        if result.song_id != song.id or result.session_id != latest_session_id:
            shell._creative_suggestion_result = None
            result = None

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
        f'{_result_markup(shell, result)}'
        f'{_deferred_markup(shell)}'
        '</div>'
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


def _post_defer(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    action = shell._consume_action(form.get("action", ""), "suggestion-defer")
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That Not Now action was already handled or expired."),
        )
        return
    parts = action.value.split("|", 2)
    if len(parts) != 3:
        shell._send_html(handler, 409, shell._simple_error("That Not Now action is no longer valid."))
        return
    song_id, session_id, semantic_key = parts
    result = getattr(shell, "_creative_suggestion_result", None)
    hq = shell.runtime.headquarters
    song = hq.store.active_song()
    latest = None if song is None else hq.sessions.latest_for_song(song.id)
    latest_session_id = None if latest is None else latest.id
    result_session_id = session_id or None
    if (
        not isinstance(result, CreativeSuggestion)
        or song is None
        or song.id != song_id
        or latest_session_id != result_session_id
        or result.song_id != song_id
        or result.session_id != result_session_id
        or result.semantic_key != semantic_key
    ):
        shell._send_html(
            handler,
            409,
            shell._simple_error("The Song or suggestion context changed. Reload before deferring this idea."),
        )
        return

    horizon = str(form.get("horizon", "")).strip().upper()
    if horizon not in DEFERRAL_HORIZONS:
        raise ValidationError("Choose a valid Not Now horizon")
    item_key = suggestion_item_key(semantic_key)
    if horizon == "LATER_THIS_SONG":
        kwargs = {"song_id": song.id, "anchor": suggestion_context_anchor(result.session_id)}
    elif horizon in {"NEXT_SONG", "AFTER_RELEASE"}:
        kwargs = {"song_id": song.id}
    else:
        kwargs = {}
    deferred = hq.attention_deferrals.defer(item_key, horizon, **kwargs)
    shell._creative_suggestion_result = None
    shell._consumer_notice = f"Not Now remembered: {_HORIZON_LABELS[deferred.horizon]}."
    shell._redirect(handler, "/song")


def _post_restore(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    action = shell._consume_action(form.get("action", ""), "suggestion-restore")
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That restore action was already handled or expired."),
        )
        return
    semantic_key = suggestion_semantic_key(action.value)
    if semantic_key is None:
        shell._send_html(handler, 409, shell._simple_error("That deferred suggestion is no longer recognized."))
        return
    restored = shell.runtime.headquarters.attention_deferrals.restore(action.value)
    shell._consumer_notice = (
        "That suggestion was already available."
        if restored is None
        else f"{suggestion_title(semantic_key)} can be suggested again."
    )
    shell._redirect(handler, "/song")


def _authorized_form(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
) -> Mapping[str, str] | None:
    if not shell._request_host_is_exact(handler) or not shell._post_origin_is_allowed(handler):
        shell._send_html(
            handler,
            403,
            shell._simple_error("That action did not come from this N0TE window."),
        )
        return None
    form = shell._read_form(handler)
    if form is None or not shell._form_authorized(form):
        shell._send_html(
            handler,
            403,
            shell._simple_error("That action expired. Reload N0TE and try again."),
        )
        return None
    return form


def install_song_creative_suggestions() -> None:
    """Attach local suggestions plus durable Attention deferral controls to /song."""
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
        path = self._path(handler)
        handlers = {
            "/suggestion/create": _post_suggestion,
            "/suggestion/defer": _post_defer,
            "/suggestion/restore": _post_restore,
        }
        if path not in handlers:
            original_post(self, handler)
            return
        form = _authorized_form(self, handler)
        if form is None:
            return
        try:
            handlers[path](self, handler, form)
        except (ValidationError, CreativeSuggestionError, ConsumerShellError) as exc:
            self._consumer_notice = str(exc)
            self._redirect(handler, "/song")
        except Exception:
            self._send_html(
                handler,
                500,
                self._simple_error(
                    "N0TE stopped that suggestion action before it could become an unclear consumer state."
                ),
            )

    ConsumerShell._song_content = with_suggestion_card
    ConsumerShell._handle_post = with_suggestion_post
    ConsumerShell._song_creative_suggestions_installed = True
