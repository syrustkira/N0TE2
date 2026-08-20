#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "CORE-04" or state.get("active_increment") != "CORE-04E":
    raise SystemExit(f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}")

from n0te2 import (
    ActionIntent, AuthorityService, ExecutionEligibilityEvidence,
    ExecutionEligibilityGate, ExecutionEligibilityRequest, HeadquartersMemory,
    NetworkPolicy, NetworkRoute, OperationError,
)

with tempfile.TemporaryDirectory() as temp:
    hq = HeadquartersMemory.create(Path(temp), "Artist")
    song = hq.store.create_song("Eligibility Song")
    version = hq.store.create_version(song.id, label="v1")
    intent = ActionIntent(
        action_id="action:provider:publish-master", job_id="job:publish-master",
        action_class="IRREVERSIBLE", description="Publish this exact approved master",
        target_ref=f"version:{version.id}", revision_fingerprint="sha256:revision-v1",
        payload_fingerprint="sha256:master-v1",
        destination="provider:distribution:selected-release",
        purpose="Publish this exact approved master",
        data_categories=("MASTER_AUDIO", "RELEASE_METADATA"),
    )
    approval = AuthorityService.bind_approval(intent, "artist-confirmation:eligibility-smoke")
    subject = "provider:distribution/account:artist"
    capability = "release.publish"
    operation = hq.operations.prepare(
        idempotency_key="idem:publish:v1", intent=intent, approval=approval,
        song_id=song.id, version_id=version.id,
        transport_route_id="route:distribution",
        eligibility_subject_id=subject,
        eligibility_capability=capability,
    )
    transport = NetworkPolicy("CONNECTED").evaluate(
        NetworkRoute("route:distribution", "INTERNET", "Distribution provider")
    )
    request = ExecutionEligibilityRequest(
        job_id=intent.job_id, route_id="route:distribution", subject_id=subject,
        capability=capability, environment_fingerprint="env:provider-v3:publish-scope",
        max_evidence_age_seconds=300,
    )
    evidence = ExecutionEligibilityEvidence(
        job_id=intent.job_id, route_id="route:distribution", subject_id=subject,
        capability=capability, environment_fingerprint="env:provider-v3:publish-scope",
        evidence_fingerprint="sha256:eligibility-v1", evidence_ref="provider-capability-check:1",
        verified=True, entitlement_state="GRANTED", permission_state="GRANTED",
        evidence_age_seconds=301,
    )
    stale = ExecutionEligibilityGate.evaluate(request, evidence)
    assert stale.status == "STALE"
    try:
        hq.operations.claim_execution(
            operation.operation_id, intent=intent, approval=approval,
            claim_evidence_ref=stale.evidence_ref,
            transport_decision=transport, eligibility_decision=stale,
        )
    except OperationError:
        pass
    else:
        raise AssertionError("stale eligibility was allowed to claim execution")

    fresh_evidence = ExecutionEligibilityEvidence(
        job_id=intent.job_id, route_id="route:distribution", subject_id=subject,
        capability=capability, environment_fingerprint="env:provider-v3:publish-scope",
        evidence_fingerprint="sha256:eligibility-v2", evidence_ref="provider-capability-check:2",
        verified=True, entitlement_state="GRANTED", permission_state="GRANTED",
        evidence_age_seconds=5,
    )
    fresh = ExecutionEligibilityGate.evaluate(request, fresh_evidence)
    assert fresh.status == "ALLOW"
    assert fresh.action_authority_granted is False
    claimed = hq.operations.claim_execution(
        operation.operation_id, intent=intent, approval=approval,
        claim_evidence_ref=fresh.evidence_ref,
        transport_decision=transport, eligibility_decision=fresh,
    )
    assert claimed.recorded_state == "EXECUTING"
    assert claimed.attempt_count == 1
    hq.close()

public = {name for name in dir(ExecutionEligibilityGate) if not name.startswith("_") and callable(getattr(ExecutionEligibilityGate, name))}
assert public == {"evaluate"}
print("CORE-04E CONSUMER SMOKE: GREEN: exact approved+connected operation was denied on stale capability/access evidence, allowed only after fresh exact entitlement/permission evidence, and eligibility granted no action authority or execution API")
