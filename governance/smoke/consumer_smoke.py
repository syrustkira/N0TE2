#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "CORE-04" or state.get("active_increment") != "CORE-04F":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2 import ActionIntent, AuthorityService, HeadquartersMemory  # noqa: E402
from n0te2.transactions import (  # noqa: E402
    CompensationResult,
    PostconditionResult,
    StepExecution,
    TransactionPlan,
    TransactionReceipt,
    TransactionSnapshot,
    TransactionStep,
)


class Driver:
    def __init__(self):
        self.events = []

    def prepare_snapshot(self, plan):
        self.events.append(("snapshot", plan.transaction_id))
        return TransactionSnapshot(
            plan.transaction_id,
            plan.operation_id,
            "snapshot:smoke",
            "sha256:snapshot-smoke",
            "evidence:snapshot:smoke",
        )

    def execute_step(self, step):
        self.events.append(("execute", step.step_id))
        if step.step_id == "step:2":
            return StepExecution(
                step.step_id,
                "FAILED",
                "APPLIED",
                "evidence:step2:partial-failure",
            )
        return StepExecution(
            step.step_id,
            "SUCCEEDED",
            "APPLIED",
            f"evidence:execute:{step.step_id}",
            f"sha256:result:{step.step_id}",
        )

    def verify_postcondition(self, step, execution):
        self.events.append(("verify", step.step_id))
        return PostconditionResult(
            step.step_id,
            step.postcondition_ref,
            "SATISFIED",
            f"evidence:post:{step.step_id}",
        )

    def compensate_step(self, step, snapshot):
        self.events.append(("compensate", step.step_id))
        return CompensationResult(
            step.step_id,
            snapshot.snapshot_ref,
            "RESTORED",
            f"evidence:compensate:{step.step_id}",
        )

    def success_receipt(self, plan, snapshot):
        raise AssertionError("success receipt must not be requested after a failed step")


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    hq = HeadquartersMemory.create(root, "Artist")
    profile_id = hq.store.profile_id
    song = hq.store.create_song("Bounded Transaction Song")
    version = hq.store.create_version(song.id, label="v1")
    intent = ActionIntent(
        action_id="action:local:bounded-routing",
        job_id="job:bounded-routing",
        action_class="REVERSIBLE",
        description="Apply one exact three-step local routing change",
        target_ref=f"version:{version.id}",
        revision_fingerprint="sha256:txn-revision-v1",
        payload_fingerprint="sha256:txn-plan-v1",
    )
    approval = AuthorityService.bind_approval(intent, "artist-confirmation:txn-smoke")
    operation = hq.operations.prepare(
        idempotency_key="idem:txn-smoke:v1",
        intent=intent,
        approval=approval,
        song_id=song.id,
        version_id=version.id,
    )
    operation = hq.operations.claim_execution(
        operation.operation_id,
        intent=intent,
        approval=approval,
        claim_evidence_ref="execution-gate:txn-smoke",
    )

    plan = TransactionPlan(
        "txn:smoke",
        operation.operation_id,
        (
            TransactionStep("step:1", "Create object", "post:object-exists", True),
            TransactionStep(
                "step:2",
                "Route object",
                "post:route-matches",
                True,
                ("step:1",),
            ),
            TransactionStep(
                "step:3",
                "Apply final value",
                "post:value-matches",
                True,
                ("step:2",),
            ),
        ),
    )
    driver = Driver()
    result = hq.transactions.run(plan, driver)

    assert result.status == "COMPENSATED"
    assert result.operation.recorded_state == "FAILED"
    assert result.executed_step_ids == ("step:1", "step:2")
    assert result.compensated_step_ids == ("step:2", "step:1")
    assert ("execute", "step:3") not in driver.events
    assert driver.events.index(("compensate", "step:2")) < driver.events.index(
        ("compensate", "step:1")
    )

    history = hq.transactions.history("txn:smoke")
    assert history.plan_fingerprint == plan.plan_fingerprint
    assert history.unresolved_execution_step_ids == ()
    assert history.unresolved_compensation_step_ids == ()
    assert history.requires_recovery_review is False
    assert any(event.event_type == "STEP_EXECUTION_STARTED" for event in history.events)
    assert any(event.event_type == "COMPENSATION_RECORDED" for event in history.events)

    hq.close()
    hq = HeadquartersMemory.open(root, profile_id)
    reopened = hq.transactions.history("txn:smoke")
    assert reopened.steps == plan.steps
    assert reopened.operation.recorded_state == "FAILED"
    assert reopened.requires_recovery_review is False
    hq.close()

print(
    "CORE-04F CONSUMER SMOKE: GREEN: a middle-step partial failure stopped forward execution, restored changed steps in strict reverse order, recorded whole-job failure rather than success, and preserved transaction recovery evidence across Headquarters restart"
)
