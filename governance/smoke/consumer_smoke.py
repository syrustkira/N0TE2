#!/usr/bin/env python3
"""Stage-aware construction smoke for the active bounded consumer outcome."""
from __future__ import annotations

import json
import sys
import tempfile
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

if active != "CORE-04" or increment != "CORE-04D":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import (  # noqa: E402
    ActionIntent,
    AuthorityService,
    DuplicateExecutionError,
    HeadquartersMemory,
    NetworkPolicy,
    NetworkRoute,
    OperationError,
)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    hq = HeadquartersMemory.create(root, "Artist")
    profile_id = hq.store.profile_id
    song = hq.store.create_song("Execute Once Song")
    version = hq.store.create_version(song.id, label="v1")

    intent = ActionIntent(
        action_id="action:provider:publish-master",
        job_id="job:publish-master",
        action_class="IRREVERSIBLE",
        description="Publish this exact approved master",
        target_ref=f"version:{version.id}",
        revision_fingerprint="sha256:revision-v1",
        payload_fingerprint="sha256:master-v1",
        destination="provider:distribution:selected-release",
        purpose="Publish this exact approved master",
        data_categories=("MASTER_AUDIO", "RELEASE_METADATA"),
    )
    approval = AuthorityService.bind_approval(
        intent, "artist-confirmation:operation-smoke"
    )

    operation = hq.operations.prepare(
        idempotency_key="idem:publish-master:v1",
        intent=intent,
        approval=approval,
        song_id=song.id,
        version_id=version.id,
        transport_route_id="route:distribution",
    )
    assert operation.recorded_state == "PREPARED"
    assert operation.attempt_count == 0
    assert operation.transport_route_id == "route:distribution"

    # Exact duplicate preparation returns the same operation identity.
    assert hq.operations.prepare(
        idempotency_key="idem:publish-master:v1",
        intent=intent,
        approval=approval,
        song_id=song.id,
        version_id=version.id,
        transport_route_id="route:distribution",
    ).operation_id == operation.operation_id

    wrong_route = NetworkPolicy("CONNECTED").evaluate(
        NetworkRoute("route:other", "INTERNET", "Unrelated provider route")
    )
    try:
        hq.operations.claim_execution(
            operation.operation_id,
            intent=intent,
            approval=approval,
            claim_evidence_ref="gate:wrong-route",
            transport_decision=wrong_route,
        )
    except OperationError:
        pass
    else:
        raise AssertionError("wrong transport route was allowed to claim execution")

    allowed = NetworkPolicy("CONNECTED").evaluate(
        NetworkRoute("route:distribution", "INTERNET", "Distribution provider")
    )
    claimed = hq.operations.claim_execution(
        operation.operation_id,
        intent=intent,
        approval=approval,
        claim_evidence_ref="gate:exact-approval-and-route",
        transport_decision=allowed,
    )
    assert claimed.recorded_state == "EXECUTING"
    assert claimed.attempt_count == 1
    assert allowed.action_authority_granted is False

    # An ambiguous result becomes UNKNOWN, never an automatic retry.
    unknown = hq.operations.mark_unknown(
        operation.operation_id,
        evidence_ref="transport-timeout:provider-outcome-ambiguous",
    )
    assert unknown.recorded_state == "UNKNOWN"
    assert unknown.effective_outcome == "UNKNOWN"
    try:
        hq.operations.claim_execution(
            operation.operation_id,
            intent=intent,
            approval=approval,
            claim_evidence_ref="gate:blind-retry",
            transport_decision=allowed,
        )
    except DuplicateExecutionError:
        pass
    else:
        raise AssertionError("UNKNOWN operation was allowed to retry")

    # Later observation may reconcile outcome without erasing UNKNOWN history.
    reconciled = hq.operations.reconcile_unknown(
        operation.operation_id,
        observed_outcome="SUCCEEDED",
        evidence_ref="provider-status-query:confirmed",
        receipt_ref="provider-receipt:confirmed",
        result_fingerprint="sha256:provider-result-v1",
    )
    assert reconciled.recorded_state == "UNKNOWN"
    assert reconciled.effective_outcome == "SUCCEEDED"
    assert reconciled.reconciled is True
    assert [event.event_type for event in hq.operations.events(operation.operation_id)] == [
        "PREPARED",
        "EXECUTION_CLAIMED",
        "UNKNOWN",
        "RECONCILED_SUCCEEDED",
    ]

    checkpoint_events = [
        event.event_type
        for event in hq.activity.for_song(song.id)
        if event.object_type == "OPERATION" and event.object_id == operation.operation_id
    ]
    assert checkpoint_events == [
        "OPERATION_PREPARED",
        "OPERATION_EXECUTION_CLAIMED",
        "OPERATION_UNKNOWN",
        "OPERATION_RECONCILED_SUCCEEDED",
    ]

    hq.close()
    hq = HeadquartersMemory.open(root, profile_id)
    reopened = hq.operations.get(operation.operation_id)
    assert reopened.recorded_state == "UNKNOWN"
    assert reopened.effective_outcome == "SUCCEEDED"
    assert reopened.receipt_ref == "provider-receipt:confirmed"
    assert reopened.attempt_count == 1
    hq.close()

print(
    "CORE-04D CONSUMER SMOKE: GREEN: exact approval plus exact route produced one durable execution claim; ambiguous outcome became UNKNOWN with no retry, later receipt evidence reconciled success without erasing UNKNOWN history, and the journal survived restart"
)
