from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler
from typing import Callable, Mapping

from .consumer_shell import ConsumerShell, ConsumerShellError
from .lineage import ValidationError
from .monitoring_context import (
    DEFAULT_MONITORING_KEYS,
    MonitoringContext,
    MonitoringContextError,
    MonitoringContextService,
)

_MONITORING_ACTION_KIND = "monitoring-context-record"
_MAX_MONITORING_VALUE = 500
_KEY_LABELS = {
    "monitoring.output_path": "Monitoring path",
    "monitoring.listening_environment": "Listening environment",
    "monitoring.reference_level": "Reference level",
    "monitoring.calibration": "Calibration context",
    "monitoring.listener_position": "Listener position",
    "monitoring.translation_check": "Translation check",
}
_SOURCE_LABELS = {
    "USER_DECLARED": "You told N0TE",
    "OBSERVED": "Observed in real work",
    "MEASURED": "Measured",
    "PROVIDER_VERIFIED": "Provider verified",
    "REMEMBERED": "Remembered",
    "INFERRED": "Inferred",
}
_SCOPE_LABELS = {
    "VERSION": "This exact Version",
    "SONG": "This Song",
    "ARTIST": "This Artist",
    "PROFILE": "This local Artist workspace",
}
_STATUS_COPY = {
    "UNKNOWN": ("caution", "Not represented yet"),
    "PARTIAL": ("caution", "Partially represented"),
    "CONFLICT": ("caution", "Conflicting evidence"),
    "RESOLVED": ("good", "Context represented"),
}


def _service(shell: ConsumerShell) -> MonitoringContextService:
    headquarters = shell.runtime.headquarters
    return MonitoringContextService(headquarters.store, headquarters.evidence)


def _clean_value(value: object) -> str:
    if not isinstance(value, str):
        raise MonitoringContextError("monitoring context must be text")
    text = " ".join(value.split())
    if not text:
        raise MonitoringContextError("monitoring context must not be empty")
    if len(text) > _MAX_MONITORING_VALUE:
        raise MonitoringContextError("monitoring context is too long")
    return text


def _display_value(value: object) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return "Represented value unavailable"
    text = " ".join(text.split())
    return text if len(text) <= 280 else text[:277] + "..."


def _source_summary(context_fact) -> str:
    labels = tuple(
        dict.fromkeys(
            _SOURCE_LABELS.get(
                claim.source_kind,
                claim.source_kind.replace("_", " ").title(),
            )
            for claim in context_fact.claims
        )
    )
    return " · ".join(labels)


def _fact_markup(fact) -> str:
    label = html.escape(_KEY_LABELS.get(fact.key, fact.key))
    if fact.status == "UNKNOWN":
        return (
            '<li class="stack">'
            f'<p><strong>{label}</strong></p>'
            '<p class="muted">Not represented</p>'
            '</li>'
        )
    if fact.status == "CONFLICT":
        competing = "".join(
            '<li>'
            f'{html.escape(_display_value(claim.value))} '
            f'<span class="muted">· {html.escape(_SOURCE_LABELS.get(claim.source_kind, claim.source_kind.replace("_", " ").title()))}</span>'
            '</li>'
            for claim in fact.claims
        )
        scope = (
            ""
            if fact.scope_kind is None
            else _SCOPE_LABELS.get(fact.scope_kind, fact.scope_kind.title())
        )
        return (
            '<li class="stack">'
            f'<p><strong>{label}</strong></p>'
            '<p class="status caution">Conflicting evidence</p>'
            f'<p class="muted">{html.escape(scope)}</p>'
            f'<ul class="stack" aria-label="Conflicting {label} evidence">{competing}</ul>'
            '</li>'
        )
    scope = (
        ""
        if fact.scope_kind is None
        else _SCOPE_LABELS.get(fact.scope_kind, fact.scope_kind.title())
    )
    return (
        '<li class="stack">'
        f'<p><strong>{label}</strong></p>'
        f'<p>{html.escape(_display_value(fact.value))}</p>'
        f'<p class="muted">{html.escape(scope)} · {html.escape(_source_summary(fact))}</p>'
        '</li>'
    )


def _binding(context: MonitoringContext, key: str) -> str:
    return json.dumps(
        {
            "fingerprint": context.fingerprint,
            "key": key,
            "song_id": context.song_id,
            "version_id": context.version_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _record_form(shell: ConsumerShell, context: MonitoringContext) -> str:
    buttons = "".join(
        '<button type="submit" name="action" '
        f'value="{html.escape(shell._new_action(_MONITORING_ACTION_KIND, _binding(context, key)), quote=True)}">'
        f'{html.escape(_KEY_LABELS[key])}</button>'
        for key in DEFAULT_MONITORING_KEYS
    )
    return (
        '<form class="stack" method="post" action="/monitoring/context">'
        f'{shell._csrf_hidden()}'
        '<div><label for="monitoring-value">What is true about this exact current Version while you are listening?</label>'
        f'<textarea id="monitoring-value" name="value" maxlength="{_MAX_MONITORING_VALUE}" rows="3" required></textarea></div>'
        f'<div class="row" aria-label="Monitoring fact to record">{buttons}</div>'
        '<p class="muted">Choose the fact this statement describes. Each button is a one-shot action bound to that exact fact, Song, current Version, and listening-context snapshot. This form records only what you tell N0TE. It does not turn your description into a measurement, calibration certificate, hearing-safety judgment, room-correction instruction, or processing authority.</p>'
        '</form>'
    )


def _current_context(
    shell: ConsumerShell,
) -> tuple[MonitoringContextService, MonitoringContext] | None:
    if shell.runtime.state != "RUNNING":
        return None
    store = shell.runtime.headquarters.store
    song = store.active_song()
    if song is None or song.current_version_id is None:
        return None
    version = store.get_version(song.current_version_id)
    if version is None or version.song_id != song.id:
        raise ConsumerShellError(
            "current Song Version is missing while preparing monitoring context"
        )
    service = _service(shell)
    return service, service.snapshot(song_id=song.id, version_id=version.id)


def _monitoring_card(shell: ConsumerShell) -> str:
    current = _current_context(shell)
    if current is None:
        return ""
    _, context = current
    status_class, status_copy = _STATUS_COPY[context.status]
    facts = "".join(_fact_markup(fact) for fact in context.facts)
    return (
        '<div class="card"><h2>Listening Context</h2>'
        '<p>Engineering evidence is heard somewhere. N0TE keeps the monitoring path and listening environment attached to this exact current Version so a local judgment does not silently become universal truth.</p>'
        f'<p class="status {status_class}">{html.escape(status_copy)}</p>'
        f'<ul class="stack" aria-label="Current listening context">{facts}</ul>'
        f'{_record_form(shell, context)}'
        '<p class="muted">Monitoring context is evidence context only. UNKNOWN, partial, conflicting, and represented states stay distinct. Nothing here changes audio, approves a Version, certifies a room or hearing safety, or grants DAW/provider authority.</p>'
        '</div>'
    )


def _decode_binding(raw: str) -> dict[str, str]:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MonitoringContextError("monitoring action binding is invalid") from exc
    expected = {"fingerprint", "key", "song_id", "version_id"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise MonitoringContextError("monitoring action binding is incomplete")
    if not all(
        isinstance(payload.get(key), str) and payload[key]
        for key in expected
    ):
        raise MonitoringContextError("monitoring action binding is invalid")
    if payload["key"] not in _KEY_LABELS:
        raise MonitoringContextError("monitoring fact is not supported")
    return payload


def _post_monitoring(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    if shell.runtime.state != "RUNNING":
        shell._send_html(
            handler,
            409,
            shell._simple_error(
                "Open an Artist workspace before recording monitoring context."
            ),
        )
        return
    action = shell._consume_action(
        form.get("action", ""),
        _MONITORING_ACTION_KIND,
    )
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error(
                "That monitoring action was already handled or expired."
            ),
        )
        return
    binding = _decode_binding(action.value)
    store = shell.runtime.headquarters.store
    song = store.active_song()
    if (
        song is None
        or song.id != binding["song_id"]
        or song.current_version_id != binding["version_id"]
    ):
        shell._send_html(
            handler,
            409,
            shell._simple_error(
                "The active Song or current Version changed. Reload the Song before recording monitoring context."
            ),
        )
        return
    version = store.get_version(binding["version_id"])
    if version is None or version.song_id != song.id:
        shell._send_html(
            handler,
            409,
            shell._simple_error(
                "That Version no longer belongs to the active Song."
            ),
        )
        return

    service = _service(shell)
    context = service.snapshot(song_id=song.id, version_id=version.id)
    if context.fingerprint != binding["fingerprint"]:
        shell._send_html(
            handler,
            409,
            shell._simple_error(
                "The listening context changed. Reload the Song before recording another monitoring fact."
            ),
        )
        return

    key = binding["key"]
    value = _clean_value(form.get("value", ""))
    supersedes = tuple(
        claim.id
        for claim in shell.runtime.headquarters.evidence.active_claims(
            "VERSION",
            version.id,
            key,
        )
        if claim.source_kind == "USER_DECLARED"
    )
    service.record_fact(
        scope_kind="VERSION",
        scope_id=version.id,
        key=key,
        value=value,
        source_kind="USER_DECLARED",
        confidence=1.0,
        supersedes=supersedes,
    )
    shell._consumer_notice = (
        f"{_KEY_LABELS[key]} updated as your declaration for this exact Version. "
        "No measurement, certification, or processing authority was created."
    )
    shell._redirect(handler, "/song")


def install_song_monitoring_context() -> None:
    """Attach exact-Version MonitoringContext to the Song engineering journey once."""

    if getattr(ConsumerShell, "_song_monitoring_context_installed", False):
        return

    original_song: Callable[[ConsumerShell, object], str] = ConsumerShell._song_content
    original_post: Callable[
        [ConsumerShell, BaseHTTPRequestHandler], None
    ] = ConsumerShell._handle_post

    def with_monitoring_card(self: ConsumerShell, state) -> str:
        rendered = original_song(self, state)
        card = _monitoring_card(self)
        if not card:
            return rendered
        marker = "</section>"
        if not rendered.endswith(marker):
            raise ConsumerShellError(
                "Song page structure changed before MonitoringContext could be attached safely"
            )
        return rendered[: -len(marker)] + card + marker

    def with_monitoring_post(
        self: ConsumerShell,
        handler: BaseHTTPRequestHandler,
    ) -> None:
        if self._path(handler) != "/monitoring/context":
            original_post(self, handler)
            return
        if not self._request_host_is_exact(handler) or not self._post_origin_is_allowed(
            handler
        ):
            self._send_html(
                handler,
                403,
                self._simple_error(
                    "That monitoring action did not come from this N0TE window."
                ),
            )
            return
        form = self._read_form(handler)
        if form is None or not self._form_authorized(form):
            self._send_html(
                handler,
                403,
                self._simple_error(
                    "That monitoring action expired. Reload the Song and try again."
                ),
            )
            return
        try:
            _post_monitoring(self, handler, form)
        except (MonitoringContextError, ValidationError, ConsumerShellError) as exc:
            self._consumer_notice = str(exc)
            self._redirect(handler, "/song")
        except Exception:
            self._send_html(
                handler,
                500,
                self._simple_error(
                    "N0TE stopped that monitoring action before it could become unclear evidence."
                ),
            )

    ConsumerShell._song_content = with_monitoring_card
    ConsumerShell._handle_post = with_monitoring_post
    ConsumerShell._song_monitoring_context_installed = True
