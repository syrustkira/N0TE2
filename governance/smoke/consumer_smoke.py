#!/usr/bin/env python3
"""Stage-aware construction smoke for the active bounded consumer outcome."""
from __future__ import annotations

import json
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))

state = json.loads((repo / "governance/current_state.json").read_text())
active = state["active_node"]
increment = state.get("active_increment")

if active in {"BOOT-02", "LEGACY-01"}:
    for forbidden in ("app", "src", "n0te2", "legacy"):
        path = repo / forbidden
        if path.exists() and any(path.rglob("*")):
            print(
                f"PRE-PRODUCT SMOKE: RED: product implementation appeared early: {forbidden}/",
                file=sys.stderr,
            )
            raise SystemExit(1)
    print("PRE-PRODUCT SMOKE: GREEN")
    raise SystemExit(0)

if active != "CORE-04" or increment != "CORE-04C":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import (  # noqa: E402
    NetworkPolicy,
    NetworkRoute,
    OfflineAccumulatedChange,
    PendingExternalChange,
)

# OFFLINE preserves local work while denying Internet transport.
offline = NetworkPolicy("OFFLINE")
localhost = offline.evaluate(
    NetworkRoute("route:localhost", "LOCALHOST", "Local N0TE/DAW bridge")
)
internet = offline.evaluate(
    NetworkRoute("route:provider", "INTERNET", "Remote provider")
)
assert localhost.status == "ALLOW"
assert internet.status == "DENY"
assert "OFFLINE_BLOCKS_INTERNET" in internet.reason_codes
assert localhost.action_authority_granted is False
assert internet.action_authority_granted is False

# LAN remains explicit-approval-only, independent of OFFLINE/CONNECTED state.
lan_denied = offline.evaluate(
    NetworkRoute("route:lan", "LAN", "Approved studio collaborator")
)
lan_allowed = offline.evaluate(
    NetworkRoute(
        "route:lan",
        "LAN",
        "Approved studio collaborator",
        lan_approval_ref="artist:lan-approval:session-1",
    )
)
assert lan_denied.status == "DENY"
assert lan_allowed.status == "ALLOW"
assert lan_allowed.action_authority_granted is False

# Before going OFFLINE, pending remote work cannot silently disappear.
connected = NetworkPolicy("CONNECTED")
pending = (
    PendingExternalChange(
        "change:upload",
        "UPLOAD",
        "Master upload is still unsent",
        "UNSENT",
    ),
    PendingExternalChange(
        "change:receipt",
        "PROVIDER_RECEIPT",
        "Publication receipt has not been reconciled",
        "UNRECONCILED",
    ),
)
offline_plan = connected.plan_offline_transition(reversed(pending))
assert offline_plan.status == "CHOICE_REQUIRED"
assert offline_plan.change_ids == ("change:receipt", "change:upload")

finish_first = connected.resolve_offline_transition(offline_plan, "FINISH_FIRST")
assert finish_first.next_mode == "CONNECTED"
assert finish_first.requires_external_work is True
assert finish_first.preserved_change_ids == offline_plan.change_ids
assert finish_first.performed_external_action is False

preserve = connected.resolve_offline_transition(
    offline_plan,
    "PRESERVE_AND_GO_OFFLINE",
)
assert preserve.next_mode == "OFFLINE"
assert preserve.preserved_change_ids == offline_plan.change_ids
assert preserve.performed_external_action is False

# Reconnect never auto-syncs. SYNC_NOW is a directive for a later executor only.
offline_changes = (
    OfflineAccumulatedChange(
        "change:local-song",
        "SONG_EDIT",
        "Song changed while offline",
    ),
    OfflineAccumulatedChange(
        "change:local-draft",
        "DRAFT",
        "Provider draft changed locally while offline",
    ),
)
connected_plan = offline.plan_connected_transition(reversed(offline_changes))
assert connected_plan.status == "CHOICE_REQUIRED"
assert connected_plan.change_ids == ("change:local-draft", "change:local-song")

sync_directive = offline.resolve_connected_transition(connected_plan, "SYNC_NOW")
assert sync_directive.next_mode == "CONNECTED"
assert sync_directive.reconciliation_directive == "SYNC_NOW"
assert sync_directive.requires_external_work is True
assert sync_directive.performed_external_action is False
assert sync_directive.action_authority_granted is False

postpone = offline.resolve_connected_transition(connected_plan, "POSTPONE")
assert postpone.next_mode == "CONNECTED"
assert postpone.reconciliation_directive == "POSTPONE"
assert postpone.requires_external_work is False
assert postpone.preserved_change_ids == connected_plan.change_ids

nothing = offline.plan_connected_transition()
assert nothing.status == "READY"
assert "NOTHING_TO_RECONCILE" in nothing.reason_codes
assert offline.resolve_connected_transition(nothing).performed_external_action is False

# The policy has no networking or synchronization executor.
public_methods = {
    name
    for name in dir(NetworkPolicy)
    if not name.startswith("_") and callable(getattr(NetworkPolicy, name))
}
assert public_methods == {
    "evaluate",
    "plan_offline_transition",
    "resolve_offline_transition",
    "plan_connected_transition",
    "resolve_connected_transition",
}
for forbidden in (
    "connect",
    "disconnect",
    "send",
    "upload",
    "sync",
    "publish",
    "execute",
    "request",
    "call_provider",
):
    assert forbidden not in public_methods

print(
    "CORE-04C CONSUMER SMOKE: GREEN: OFFLINE blocked Internet but preserved localhost, LAN required explicit approval, pending remote work survived the offline choice, reconnect required a reconciliation directive, SYNC_NOW performed nothing, and connectivity granted no action authority"
)
