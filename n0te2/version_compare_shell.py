from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler
from typing import Callable

from .consumer_shell import ConsumerShell, ConsumerShellError, _MediaGrant, _PageState
from .version_compare import (
    VersionCompareError,
    VersionCompareService,
    VersionCompareSide,
    VersionComparison,
)


def _service(shell: ConsumerShell) -> VersionCompareService:
    hq = shell.runtime.headquarters
    return VersionCompareService(hq.store, hq.materials)


def _format_dbfs(value: float | None) -> str:
    return "silent / unavailable" if value is None else f"{value:.2f} dBFS"


def _side_markup(shell: ConsumerShell, comparison: VersionComparison, side: VersionCompareSide, role: str) -> str:
    status = {
        "AUDITIONABLE_MEASURED": '<p class="status good">Verified local audio · exact PCM level evidence</p>',
        "AUDITIONABLE_UNMEASURED": '<p class="status good">Verified local audio · level evidence unavailable</p>',
        "NO_AUDITIONABLE_AUDIO": '<p class="status caution">No supported local audio for this Version</p>',
        "INTEGRITY_BLOCKED": '<p class="status caution">Local material could not be re-verified safely</p>',
    }[side.status]
    audition = ""
    if side.auditionable:
        assert side.asset_id is not None and side.asset_sha256 is not None
        token = shell._new_media_grant(
            _MediaGrant(
                song_id=comparison.song_id,
                version_id=side.version_id,
                asset_id=side.asset_id,
                sha256=side.asset_sha256,
            )
        )
        src = html.escape(f"/media/song-version/{token}", quote=True)
        aria = html.escape(f"Audition Version {side.ordinal}: {side.label}", quote=True)
        audition = (
            f'<audio controls preload="metadata" src="{src}" aria-label="{aria}">'
            'Your browser cannot play this local audio Version.'
            '</audio>'
        )
    evidence = ""
    if side.measured:
        evidence = (
            '<dl>'
            '<dt>Whole-render RMS</dt>'
            f'<dd>{html.escape(_format_dbfs(side.rms_dbfs))}</dd>'
            '<dt>Sample peak</dt>'
            f'<dd>{html.escape(_format_dbfs(side.sample_peak_dbfs))}</dd>'
            '</dl>'
            '<p class="muted">Observed from this exact verified integer PCM WAV. Whole-render RMS is not LUFS.</p>'
        )
    elif side.auditionable:
        evidence = (
            '<p class="muted">You can audition this exact verified local file, but N0TE has no supported exact PCM level measurement for it.</p>'
        )
    return (
        '<div class="card stack">'
        f'<p class="eyebrow">{html.escape(role)}</p>'
        f'<h2>Version {side.ordinal}: {html.escape(side.label)}</h2>'
        f'{status}{audition}{evidence}'
        '</div>'
    )


def _level_markup(comparison: VersionComparison) -> str:
    if comparison.rms_delta_db is None:
        return (
            '<p class="status caution">No trustworthy whole-render RMS difference is available for this pair.</p>'
            '<p>Match playback or monitor level by ear before judging. N0TE will not invent a loudness match.</p>'
        )
    delta = comparison.rms_delta_db
    if abs(delta) < 0.05:
        statement = "The two measured whole-render RMS levels are within 0.05 dB."
    elif delta > 0:
        statement = f"The Current Version whole-render RMS is {abs(delta):.2f} dB higher than the Reference Version."
    else:
        statement = f"The Reference Version whole-render RMS is {abs(delta):.2f} dB higher than the Current Version."
    return (
        f'<p class="status caution">{html.escape(statement)}</p>'
        '<p>That difference can bias an A/B toward the louder render. RMS is not LUFS, and N0TE has applied no gain or normalization. Level-match deliberately before deciding.</p>'
    )


def _compare_content(shell: ConsumerShell) -> str:
    comparison = _service(shell).prepare()
    if comparison.status == "NO_CURRENT_VERSION":
        return (
            '<section class="grid"><div class="card"><h2>No current Version to compare</h2>'
            '<p>Add real Song material first. Comparison is read-only and does not create placeholder Versions.</p>'
            '<a class="button primary" href="/song">Return to Song</a></div></section>'
        )
    assert comparison.current is not None
    if comparison.status == "NO_REFERENCE_VERSION":
        return (
            '<section class="grid">'
            f'{_side_markup(shell, comparison, comparison.current, "Current")}'
            '<div class="card"><h2>A second Version is needed</h2>'
            '<p>Preserve another Version of this Song before A/B. N0TE will not manufacture a fake alternate merely to fill the comparison.</p>'
            '<a class="button primary" href="/song">Return to Song</a></div></section>'
        )
    assert comparison.reference is not None
    limitations = "".join(f'<li>{html.escape(item)}</li>' for item in comparison.limitations)
    readiness = (
        '<p class="status good">Both exact Versions are locally auditionable</p>'
        if comparison.both_auditionable
        else '<p class="status caution">This pair is only partially auditionable</p>'
    )
    return (
        '<section class="grid" aria-label="Version A/B comparison">'
        '<div class="card stack"><h2>Before you judge</h2>'
        f'{readiness}{_level_markup(comparison)}'
        '<p><strong>Nothing has been chosen.</strong> Listening here does not approve, reject, revert, branch, learn a preference, or change the current Version.</p>'
        f'<details><summary>Evidence boundary</summary><ul class="stack">{limitations}</ul></details>'
        '</div>'
        f'{_side_markup(shell, comparison, comparison.reference, "Reference")}'
        f'{_side_markup(shell, comparison, comparison.current, "Current")}'
        '<div class="card"><h2>Artist decision remains next</h2>'
        '<p>Use this surface to hear the exact pair and remove obvious level bias. KEEP, REVERT, REVISE, or INCONCLUSIVE remains a separate explicit decision step.</p>'
        '<a class="button" href="/song">Back to Song history</a></div>'
        '</section>'
    )


def _compare_song_card(shell: ConsumerShell) -> str:
    if shell.runtime.state != "RUNNING":
        return ""
    song = shell.runtime.headquarters.store.active_song()
    if song is None or song.current_version_id is None:
        return ""
    versions = shell.runtime.headquarters.store.versions_for_song(song.id)
    if len(versions) < 2:
        return ""
    return (
        '<div class="card"><h2>Compare Versions</h2>'
        '<p>Hear the current Version beside one exact lineage neighbor. When both are measurable PCM WAVs, N0TE shows whole-render RMS difference only to flag possible level bias.</p>'
        '<a class="button primary" href="/compare">Open A/B compare</a>'
        '<p class="muted">Read-only. RMS is not LUFS. No gain, approval, Learning result, provider call, or DAW change is applied.</p>'
        '</div>'
    )


def _compare_state(shell: ConsumerShell) -> _PageState:
    artist = shell.runtime.headquarters.store.artist()
    song = shell.runtime.headquarters.store.active_song()
    if song is None:
        return _PageState(
            "running-compare",
            "Compare Versions",
            "Hear · Compare",
            "Start a Song before comparing exact Versions.",
            artist_name=artist.display_name,
        )
    return _PageState(
        "running-compare",
        "Compare Versions",
        "Hear · Compare",
        "A/B exact local Song Versions without letting a louder render masquerade as a better artistic decision.",
        artist_name=artist.display_name,
        song_title=song.title,
    )


def install_song_version_compare() -> None:
    """Attach the bounded read-only Version A/B surface exactly once."""
    if getattr(ConsumerShell, "_song_version_compare_installed", False):
        return

    original_song: Callable[[ConsumerShell, object], str] = ConsumerShell._song_content
    original_state_content: Callable[[ConsumerShell, object], str] = ConsumerShell._state_content
    original_get: Callable[[ConsumerShell, BaseHTTPRequestHandler], None] = ConsumerShell._handle_get

    def with_compare_card(self: ConsumerShell, state) -> str:
        rendered = original_song(self, state)
        marker = "</section>"
        if not rendered.endswith(marker):
            raise ConsumerShellError("Song page structure changed before Version compare could attach safely")
        return rendered[: -len(marker)] + _compare_song_card(self) + marker

    def with_compare_content(self: ConsumerShell, state) -> str:
        if state.kind != "running-compare":
            return original_state_content(self, state)
        if state.song_title is None:
            return (
                '<section class="grid"><div class="card"><h2>No active Song</h2>'
                '<p>Start or select a Song before comparing Versions.</p>'
                '<a class="button primary" href="/song">Go to Song</a></div></section>'
            )
        return _compare_content(self)

    def with_compare_get(self: ConsumerShell, handler: BaseHTTPRequestHandler) -> None:
        if self._path(handler) != "/compare":
            original_get(self, handler)
            return
        if not self._request_host_is_exact(handler):
            self._send_html(
                handler,
                421,
                self._simple_error("This N0TE window is available only from its exact local address."),
            )
            return
        try:
            if self.runtime.state != "RUNNING":
                state = self._ensure_runtime()
                if state is not None:
                    self._send_html(handler, 200, self._render_state(state, path="/"))
                    return
            self._send_html(handler, 200, self._render_state(_compare_state(self), path="/song"))
        except (VersionCompareError, ConsumerShellError):
            self._send_html(
                handler,
                409,
                self._simple_error("The Song Version state changed while A/B was being prepared. Reload the Song and try again."),
            )
        except Exception:
            self._send_html(
                handler,
                500,
                self._simple_error("N0TE stopped the comparison before uncertain media or evidence could be presented as exact."),
            )

    ConsumerShell._song_content = with_compare_card
    ConsumerShell._state_content = with_compare_content
    ConsumerShell._handle_get = with_compare_get
    ConsumerShell._song_version_compare_installed = True
