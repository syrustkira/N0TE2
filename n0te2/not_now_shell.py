from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler
from typing import Callable, Mapping

from .attention_deferral import DEFERRAL_HORIZONS
from .consumer_shell import ConsumerShell, ConsumerShellError

_ITEM_KEY = "NOW_THREAD"
_HORIZON_ORDER = (
    "LATER_THIS_SONG",
    "AFTER_RELEASE",
    "NEXT_SONG",
    "SOMEDAY",
    "NEVER_SUGGEST_AGAIN",
)
_HORIZON_LABELS = {
    "LATER_THIS_SONG": "Later this Song",
    "AFTER_RELEASE": "After release",
    "NEXT_SONG": "Next Song",
    "SOMEDAY": "Someday",
    "NEVER_SUGGEST_AGAIN": "Never suggest again",
}


def _thread_binding(shell: ConsumerShell) -> tuple[str | None, str]:
    if shell.runtime.state != "RUNNING":
        raise ConsumerShellError("Not Now requires an open Artist workspace")
    song = shell.runtime.headquarters.store.active_song()
    if song is None:
        return None, "NO_ACTIVE_SONG"
    latest = shell.runtime.headquarters.sessions.latest_for_song(song.id)
    if latest is None:
        return song.id, f"SONG:{song.id}:NO_SESSION"
    return song.id, f"SONG:{song.id}:SESSION:{latest.id}:{latest.state}"


def _action_binding(horizon: str, song_id: str | None, anchor: str) -> tuple[str | None, str | None]:
    if horizon == "LATER_THIS_SONG":
        return song_id, anchor
    if horizon in {"AFTER_RELEASE", "NEXT_SONG"}:
        return song_id, None
    return None, None


def _defer_controls(shell: ConsumerShell, song_id: str, anchor: str) -> str:
    forms: list[str] = []
    for horizon in _HORIZON_ORDER:
        bound_song, bound_anchor = _action_binding(horizon, song_id, anchor)
        value = json.dumps(
            [horizon, bound_song, bound_anchor], separators=(",", ":")
        )
        token = shell._new_action("not-now-defer", value)
        forms.append(
            '<form method="post" action="/not-now/defer">'
            f'{shell._hidden(token)}'
            f'<button type="submit">{html.escape(_HORIZON_LABELS[horizon])}</button>'
            '</form>'
        )
    return (
        '<details><summary>Not now</summary>'
        '<p class="muted">Move this N0TE continuation prompt out of your current attention without deleting the Song, Session, next action, or memory behind it.</p>'
        '<div class="actions">'
        + "".join(forms)
        + '</div></details>'
    )


def _deferred_card(shell: ConsumerShell, horizon: str, deferral_id: str) -> str:
    token = shell._new_action("not-now-restore", deferral_id)
    label = _HORIZON_LABELS[horizon]
    if horizon == "AFTER_RELEASE":
        detail = (
            "Held for the release boundary. It remains suppressed until verified release "
            "state makes it eligible or you bring it back yourself."
        )
    elif horizon == "NEXT_SONG":
        detail = "This prompt stays out of NOW for the current Song and becomes eligible again on the next Song."
    elif horizon == "LATER_THIS_SONG":
        detail = "This exact continuation stays quiet until the Song's work-Session thread changes."
    elif horizon == "NEVER_SUGGEST_AGAIN":
        detail = "This kind of continuation prompt stays suppressed until you explicitly bring it back."
    else:
        detail = "N0TE remembers the thread without pushing it into your current attention."
    return (
        '<div class="card"><h2>Pick up the thread</h2>'
        f'<p class="status caution">Deferred · {html.escape(label)}</p>'
        f'<p>{html.escape(detail)}</p>'
        '<form method="post" action="/not-now/restore">'
        f'{shell._hidden(token)}'
        '<button type="submit">Bring it back</button></form>'
        '</div>'
    )


def _decorate_now(shell: ConsumerShell, rendered: str) -> str:
    marker = '<section class="grid">'
    card_marker = '<div class="card"><h2>Pick up the thread</h2>'
    if not rendered.startswith(marker):
        raise ConsumerShellError("NOW structure changed before Not Now could attach safely")
    start = rendered.find(card_marker, len(marker))
    if start != len(marker):
        raise ConsumerShellError("Pick up the thread is no longer the first NOW card")
    end = rendered.find("</div>", start)
    if end < 0:
        raise ConsumerShellError("Pick up the thread card is incomplete")
    end += len("</div>")

    song_id, anchor = _thread_binding(shell)
    memory = shell.runtime.headquarters.attention_deferrals
    active = memory.active(_ITEM_KEY)
    if active is not None and memory.applies(
        _ITEM_KEY,
        song_id=song_id,
        anchor=anchor,
        released_song_ids=(),
    ):
        return rendered[:start] + _deferred_card(shell, active.horizon, active.id) + rendered[end:]
    if song_id is None:
        return rendered
    original = rendered[start:end]
    if not original.endswith("</div>"):
        raise ConsumerShellError("Pick up the thread card could not accept Not Now controls")
    decorated = original[:-len("</div>")] + _defer_controls(shell, song_id, anchor) + "</div>"
    return rendered[:start] + decorated + rendered[end:]


def _authorize_not_now(
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
    if shell.runtime.state != "RUNNING":
        shell._send_html(
            handler,
            409,
            shell._simple_error("Open an Artist workspace before changing Not Now."),
        )
        return None
    return form


def _post_defer(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    action = shell._consume_action(form.get("action", ""), "not-now-defer")
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That Not Now choice was already handled or expired."),
        )
        return
    try:
        decoded = json.loads(action.value)
    except json.JSONDecodeError:
        decoded = None
    if not isinstance(decoded, list) or len(decoded) != 3:
        shell._send_html(handler, 409, shell._simple_error("That Not Now choice is no longer valid."))
        return
    horizon, expected_song, expected_anchor = decoded
    if horizon not in DEFERRAL_HORIZONS:
        shell._send_html(handler, 409, shell._simple_error("That Not Now horizon is no longer valid."))
        return
    current_song, current_anchor = _thread_binding(shell)
    bound_song, bound_anchor = _action_binding(str(horizon), current_song, current_anchor)
    if bound_song != expected_song or bound_anchor != expected_anchor:
        shell._send_html(
            handler,
            409,
            shell._simple_error("The Song thread changed. Reload NOW before deferring it."),
        )
        return
    shell.runtime.headquarters.attention_deferrals.defer(
        _ITEM_KEY,
        str(horizon),
        song_id=bound_song,
        anchor=bound_anchor,
    )
    shell._consumer_notice = f"Pick up the thread moved to {_HORIZON_LABELS[str(horizon)]}."
    shell._redirect(handler, "/now")


def _post_restore(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    action = shell._consume_action(form.get("action", ""), "not-now-restore")
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That restore action was already handled or expired."),
        )
        return
    active = shell.runtime.headquarters.attention_deferrals.active(_ITEM_KEY)
    if active is None or active.id != action.value:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That deferred thread changed. Reload NOW before restoring it."),
        )
        return
    shell.runtime.headquarters.attention_deferrals.restore(_ITEM_KEY)
    shell._consumer_notice = "Pick up the thread is back in NOW."
    shell._redirect(handler, "/now")


def install_not_now() -> None:
    """Attach bounded durable deferral to the existing NOW continuation item."""
    if getattr(ConsumerShell, "_not_now_installed", False):
        return

    original_focus: Callable[[ConsumerShell, object], str] = ConsumerShell._focus_content
    original_post = ConsumerShell._handle_post

    def with_not_now(self: ConsumerShell, state) -> str:
        return _decorate_now(self, original_focus(self, state))

    def with_not_now_post(self: ConsumerShell, handler: BaseHTTPRequestHandler) -> None:
        path = self._path(handler)
        if path not in {"/not-now/defer", "/not-now/restore"}:
            original_post(self, handler)
            return
        form = _authorize_not_now(self, handler)
        if form is None:
            return
        try:
            if path == "/not-now/defer":
                _post_defer(self, handler, form)
            else:
                _post_restore(self, handler, form)
        except ConsumerShellError as exc:
            self._consumer_notice = str(exc)
            self._redirect(handler, "/now")
        except Exception:
            self._send_html(
                handler,
                500,
                self._simple_error("N0TE stopped that Not Now change before attention state became unclear."),
            )

    ConsumerShell._focus_content = with_not_now
    ConsumerShell._handle_post = with_not_now_post
    ConsumerShell._not_now_installed = True
