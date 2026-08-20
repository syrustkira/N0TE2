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

if active != "CORE-04" or increment != "CORE-04A":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import ActionIntent, AuthorityService  # noqa: E402

intent = ActionIntent(
    action_id="action:publish:master-v7",
    job_id="job:publish-release-master",
    action_class="IRREVERSIBLE",
    description="Publish the approved master to the selected release destination",
    target_ref="song:song-1/version:v7",
    revision_fingerprint="sha256:revision-v7",
    payload_fingerprint="sha256:rendered-master-v7",
    destination="provider:distribution:selected-release",
    purpose="Publish this exact approved master for release",
    data_categories=("MASTER_AUDIO", "RELEASE_METADATA", "MASTER_AUDIO"),
)

service = AuthorityService()
preview = service.preview(intent)
assert preview.intent_fingerprint == intent.intent_fingerprint
assert preview.data_categories == ("MASTER_AUDIO", "RELEASE_METADATA")

approval = service.bind_approval(intent, source_ref="artist-confirmation:approval-screen:42")
valid = service.validate(intent, approval)
assert valid.status == "VALID"
assert valid.bound_intent_fingerprint == preview.intent_fingerprint

# A material revision change makes the old approval unusable.
stale_intent = replace(
    intent,
    revision_fingerprint="sha256:revision-v8",
    payload_fingerprint="sha256:rendered-master-v8",
)
stale = service.validate(stale_intent, approval)
assert stale.status == "STALE"
assert stale.current_intent_fingerprint != stale.bound_intent_fingerprint

# Non-material category order/duplication canonicalizes to the same fingerprint.
reordered = replace(
    intent,
    data_categories=("RELEASE_METADATA", "MASTER_AUDIO", "MASTER_AUDIO"),
)
assert reordered.intent_fingerprint == intent.intent_fingerprint
assert service.validate(reordered, approval).status == "VALID"

# A local reversible action can omit destination/purpose but still gains no executor.
local = ActionIntent(
    action_id="action:local:rename-version",
    job_id="job:rename-version",
    action_class="REVERSIBLE",
    description="Rename the current local Song version",
    target_ref="song:song-1/version:v7",
    revision_fingerprint="sha256:revision-v7",
    payload_fingerprint="sha256:rename-payload",
)
local_approval = service.bind_approval(local, source_ref="artist-confirmation:local:1")
assert service.validate(local, local_approval).status == "VALID"

# Authority binding is not execution permission. The public service owns no action verb.
public_methods = {
    name
    for name in dir(AuthorityService)
    if not name.startswith("_") and callable(getattr(AuthorityService, name))
}
assert public_methods == {"preview", "bind_approval", "validate"}
for forbidden in ("execute", "send", "post", "publish", "mutate", "charge"):
    assert not hasattr(service, forbidden)

print(
    "CORE-04A CONSUMER SMOKE: GREEN: exact action preview bound approval to one material fingerprint; unchanged intent stayed VALID, changed revision/payload became STALE, category ordering canonicalized, and authority exposed no execution path"
)
