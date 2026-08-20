#!/usr/bin/env python3
"""Stage-aware construction smoke for the active bounded consumer outcome."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
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

if active != "CORE-04" or increment != "CORE-04B":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import OutboundEnvelope, OutboundInspector, OutboundMaterial  # noqa: E402

master = OutboundMaterial(
    item_id="material:master-v7",
    category="UNRELEASED_AUDIO",
    source_ref="song:song-1/version:v7/asset:master",
    revision_fingerprint="sha256:master-v7",
    private=True,
    rights_ref="rights:artist-owned",
    consent_ref="consent:artist:analysis-provider",
)
notes = OutboundMaterial(
    item_id="material:private-notes",
    category="PRIVATE_ARTIST_CONTEXT",
    source_ref="song:song-1/context:mix-notes",
    revision_fingerprint="sha256:mix-notes-v3",
    private=True,
    rights_ref="rights:artist-private-context",
    consent_ref="consent:artist:analysis-provider",
)

envelope = OutboundEnvelope(
    request_id="request:mix-analysis:001",
    job_id="job:analyze-master",
    description="Analyze the exact unreleased master and selected private mix notes",
    destination="provider:model:selected-analysis",
    purpose="Return bounded mix feedback for this exact Song version",
    materials=(notes, master),
    retention_statement="Provider retention policy reviewed for this bounded request",
    cost_statement="Estimated maximum cost: $0.25",
)

inspector = OutboundInspector()
preview = inspector.preview(envelope)
assert tuple(item.item_id for item in preview.materials) == (
    "material:master-v7",
    "material:private-notes",
)
assert preview.private_material_ids == (
    "material:master-v7",
    "material:private-notes",
)
assert preview.data_categories == (
    "PRIVATE_ARTIST_CONTEXT",
    "UNRELEASED_AUDIO",
)
assert preview.destination == envelope.destination
assert preview.purpose == envelope.purpose
assert preview.retention_statement == envelope.retention_statement
assert preview.cost_statement == envelope.cost_statement

confirmation = inspector.bind_confirmation(
    envelope,
    source_ref="artist-confirmation:egress-screen:17",
)
assert inspector.validate_confirmation(envelope, confirmation).status == "VALID"

# Changing one represented consent fact invalidates the entire bounded confirmation.
changed_consent = replace(
    envelope,
    materials=(replace(master, consent_ref="consent:artist:different-scope"), notes),
)
assert inspector.validate_confirmation(changed_consent, confirmation).status == "STALE"

# Changing the exact source revision also invalidates it.
changed_revision = replace(
    envelope,
    materials=(replace(master, revision_fingerprint="sha256:master-v8"), notes),
)
assert inspector.validate_confirmation(changed_revision, confirmation).status == "STALE"

# Input order is non-material and canonicalizes.
reordered = replace(envelope, materials=tuple(reversed(envelope.materials)))
assert reordered == envelope
assert inspector.validate_confirmation(reordered, confirmation).status == "VALID"

# Inspect/confirm is still not transport.
public_methods = {
    name
    for name in dir(OutboundInspector)
    if not name.startswith("_") and callable(getattr(OutboundInspector, name))
}
assert public_methods == {"preview", "bind_confirmation", "validate_confirmation"}
for forbidden in (
    "send",
    "upload",
    "transmit",
    "request",
    "call_model",
    "execute",
    "publish",
    "post",
):
    assert not hasattr(inspector, forbidden)

print(
    "CORE-04B CONSUMER SMOKE: GREEN: exact private outbound material, destination, purpose, retention and cost were inspectable; confirmation bound to the exact package, consent/revision changes became STALE, input order canonicalized, and no transport API existed"
)
