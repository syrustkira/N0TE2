from __future__ import annotations

import html
from typing import Callable

from .consumer_shell import ConsumerShell, ConsumerShellError
from .host_installations import (
    HostInstallationError,
    HostInstallationInventory,
    NO_STANDARD_SCAN,
    runtime_host_installation_inventory,
)

_PEER_LABELS = {
    "ABLETON_LIVE": "Ableton Live",
    "FL_STUDIO": "FL Studio",
    "LOGIC_PRO": "Logic Pro",
    "PRO_TOOLS": "Pro Tools",
    "STUDIO_ONE": "Studio One",
    "REAPER": "REAPER",
}


def _inventory(shell: ConsumerShell) -> HostInstallationInventory:
    scanner = getattr(shell, "_host_installation_scanner", None)
    if scanner is None:
        result = runtime_host_installation_inventory(shell.process.platform)
    else:
        if not callable(scanner):
            raise ConsumerShellError("host installation scanner override is not callable")
        result = scanner(shell.process.platform)
    if not isinstance(result, HostInstallationInventory):
        raise ConsumerShellError("host installation scanner returned an invalid result")
    return result


def _inventory_card(shell: ConsumerShell) -> str:
    try:
        inventory = _inventory(shell)
    except HostInstallationError:
        return (
            '<div class="card"><h2>DAWs on this machine</h2>'
            '<p class="status caution">Installation evidence unavailable</p>'
            '<p>N0TE could not complete the bounded local installation scan safely. '
            'It is treating every peer DAW as UNKNOWN rather than guessing.</p>'
            '</div>'
        )

    boundary = (
        "Observed locally means only that N0TE found a safe entry in a bounded standard install location. "
        "It does not mean the DAW is open, healthy, adapter-tested, supported, or controllable."
    )
    if inventory.scan_state == NO_STANDARD_SCAN:
        return (
            '<div class="card"><h2>DAWs on this machine</h2>'
            '<p class="status caution">Standard-location scan not available here</p>'
            '<p>No bounded standard installation layout is defined for this platform yet. '
            'Every peer DAW remains UNKNOWN.</p>'
            f'<p class="muted">{html.escape(boundary)}</p></div>'
        )

    if inventory.observations:
        rows = "".join(
            '<li><strong>'
            + html.escape(item.display_name)
            + '</strong> <span class="status good">Observed locally</span></li>'
            for item in inventory.observations
        )
        unknown_labels = ", ".join(_PEER_LABELS[family] for family in inventory.unknown_families)
        unknown = (
            ""
            if not unknown_labels
            else '<p class="muted">Not observed by this bounded scan, therefore UNKNOWN: '
            + html.escape(unknown_labels)
            + ".</p>"
        )
        body = f'<ul class="stack">{rows}</ul>{unknown}'
    else:
        body = (
            '<p class="status caution">No peer DAW observed in standard locations</p>'
            '<p>That result is UNKNOWN, not proof that no DAW is installed. '
            'Custom, portable, package-managed, and other locations are outside this scan.</p>'
        )

    return (
        '<div class="card"><h2>DAWs on this machine</h2>'
        '<p>Read-only local installation evidence for peer DAWs.</p>'
        f'{body}<p class="muted">{html.escape(boundary)}</p>'
        '<p class="muted">This inventory is rescanned when Settings is rendered and is not promoted into Artist or Song memory.</p>'
        '</div>'
    )


def install_host_installation_inventory() -> None:
    """Attach read-only host installation evidence to the existing Settings surface."""

    if getattr(ConsumerShell, "_host_installation_inventory_installed", False):
        return

    original_content: Callable[[ConsumerShell, object], str] = ConsumerShell._state_content

    def with_host_installation_inventory(self: ConsumerShell, state) -> str:
        rendered = original_content(self, state)
        if getattr(state, "kind", None) != "running-settings":
            return rendered
        marker = "</section>"
        if not rendered.endswith(marker):
            raise ConsumerShellError(
                "Settings page structure changed before host installation inventory could attach safely"
            )
        return rendered[: -len(marker)] + _inventory_card(self) + marker

    ConsumerShell._state_content = with_host_installation_inventory
    ConsumerShell._host_installation_inventory_installed = True
