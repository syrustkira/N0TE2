import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from n0te2 import (
    ActionIntent,
    AuthorityService,
    DuplicateExecutionError,
    HeadquartersMemory,
    LineageCorruptionError,
    NetworkPolicy,
    NetworkRoute,
    OperationError,
    ValidationError,
)


def local_intent(version_id: str) -> ActionIntent:
    return ActionIntent(
        action_id="action:local:rename-version",
        job_id="job:rename-version",
        action_class="REVERSIBLE",
        description="Rename this exact local Song version",
        target_ref=f"version:{version_id}",
        revision_fingerprint="sha256:revision-v1",
        payload_fingerprint="sha256:rename-payload-v1",
    )


def outbound_intent(version_id: str) -> ActionIntent:
    return ActionIntent(
        action_id="action:provider:publish",
        job_id="job:publish-master",
        action_class="IRREVERSIBLE",
        description="Publish this exact approved master",
        target_ref=f"version:{version_id}",
        revision_fingerprint="sha256:revision-v1",
        payload_fingerprint="sha256:master-payload-v1",
        destination="provider:distribution:selected-release",
        purpose="Publish this exact approved master",
        data_categories=("MASTER_AUDIO", "RELEASE_METADATA"),
    )


class Core04DOperationJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.hq = HeadquartersMemory.create(self.root, "Artist")
        self.profile_id = self.hq.store.profile_id
        self.song = self.hq.store.create_song("Journal Song")
        self.version = self.hq.store.create_version(self.song.id, label="v1")
        self.intent = local_intent(self.version.id)
        self.approval = AuthorityService.bind_approval(
            self.intent, "artist-confirmation:operation:1"
        )

    def tearDown(self):
        try:
            self.hq.close()
        except Exception:
            pass
        self.temp.cleanup()

    def prepare(self, key="idem:local:1"):
        return self.hq.operations.prepare(
            idempotency_key=key,
            intent=self.intent,
            approval=self.approval,
            song_id=self.song.id,
            version_id=self.version.id,
        )

    def claim(self, operation_id):
        return self.hq.operations.claim_execution(
            operation_id,
            intent=self.intent,
            approval=self.approval,
            claim_evidence_ref="execution-gate:local:1",
        )

    def test_prepare_is_durable_and_idempotent_for_exact_same_identity(self):
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first, second)
        self.assertEqual(first.recorded_state, "PREPARED")
        self.assertEqual(first.attempt_count, 0)
        self.assertIsNone(first.effective_outcome)

        self.hq.close()
        self.hq = HeadquartersMemory.open(self.root, self.profile_id)
        reopened = self.hq.operations.get(first.operation_id)
        self.assertEqual(reopened, first)
        self.assertEqual(
            self.hq.operations.by_idempotency_key("idem:local:1"), first
        )

    def test_prepare_rejects_stale_approval_and_key_reuse_for_different_operation(self):
        self.prepare()
        changed = replace(self.intent, payload_fingerprint="sha256:different")
        with self.assertRaises(OperationError):
            self.hq.operations.prepare(
                idempotency_key="idem:stale",
                intent=changed,
                approval=self.approval,
            )
        changed_approval = AuthorityService.bind_approval(
            changed, "artist-confirmation:operation:2"
        )
        with self.assertRaises(OperationError):
            self.hq.operations.prepare(
                idempotency_key="idem:local:1",
                intent=changed,
                approval=changed_approval,
            )

    def test_exact_operation_can_be_claimed_only_once(self):
        prepared = self.prepare()
        claimed = self.claim(prepared.operation_id)
        self.assertEqual(claimed.recorded_state, "EXECUTING")
        self.assertEqual(claimed.attempt_count, 1)
        with self.assertRaises(DuplicateExecutionError):
            self.claim(prepared.operation_id)
        self.assertEqual(
            [event.event_type for event in self.hq.operations.events(prepared.operation_id)],
            ["PREPARED", "EXECUTION_CLAIMED"],
        )

    def test_outbound_claim_requires_allow_transport_decision(self):
        intent = outbound_intent(self.version.id)
        approval = AuthorityService.bind_approval(intent, "artist:publish:approval")
        operation = self.hq.operations.prepare(
            idempotency_key="idem:publish:1",
            intent=intent,
            approval=approval,
            song_id=self.song.id,
            version_id=self.version.id,
        )
        with self.assertRaises(OperationError):
            self.hq.operations.claim_execution(
                operation.operation_id,
                intent=intent,
                approval=approval,
                claim_evidence_ref="gate:no-network-decision",
            )
        denied = NetworkPolicy("OFFLINE").evaluate(
            NetworkRoute("route:provider", "INTERNET", "Distribution provider")
        )
        with self.assertRaises(OperationError):
            self.hq.operations.claim_execution(
                operation.operation_id,
                intent=intent,
                approval=approval,
                claim_evidence_ref="gate:offline",
                transport_decision=denied,
            )
        allowed = NetworkPolicy("CONNECTED").evaluate(
            NetworkRoute("route:provider", "INTERNET", "Distribution provider")
        )
        claimed = self.hq.operations.claim_execution(
            operation.operation_id,
            intent=intent,
            approval=approval,
            claim_evidence_ref="gate:connected-and-approved",
            transport_decision=allowed,
        )
        self.assertEqual(claimed.recorded_state, "EXECUTING")
        self.assertFalse(allowed.action_authority_granted)

    def test_success_receipt_persists_and_prevents_retry(self):
        operation = self.prepare()
        self.claim(operation.operation_id)
        success = self.hq.operations.complete_success(
            operation.operation_id,
            receipt_ref="provider-receipt:abc",
            evidence_ref="provider-observation:success",
            result_fingerprint="sha256:provider-result",
        )
        self.assertEqual(success.recorded_state, "SUCCEEDED")
        self.assertEqual(success.effective_outcome, "SUCCEEDED")
        self.assertEqual(success.receipt_ref, "provider-receipt:abc")
        with self.assertRaises(DuplicateExecutionError):
            self.claim(operation.operation_id)

        self.hq.close()
        self.hq = HeadquartersMemory.open(self.root, self.profile_id)
        reopened = self.hq.operations.get(operation.operation_id)
        self.assertEqual(reopened.recorded_state, "SUCCEEDED")
        self.assertEqual(reopened.receipt_ref, "provider-receipt:abc")
        self.assertEqual(reopened.attempt_count, 1)

    def test_explicit_failure_is_terminal_and_durable(self):
        operation = self.hq.operations.prepare(
            idempotency_key="idem:failure",
            intent=self.intent,
            approval=self.approval,
        )
        self.claim(operation.operation_id)
        failed = self.hq.operations.complete_failure(
            operation.operation_id,
            evidence_ref="adapter-error:explicit-failure",
            receipt_ref="provider-error-receipt:1",
        )
        self.assertEqual(failed.recorded_state, "FAILED")
        self.assertEqual(failed.effective_outcome, "FAILED")
        with self.assertRaises(OperationError):
            self.hq.operations.reconcile_unknown(
                operation.operation_id,
                observed_outcome="SUCCEEDED",
                evidence_ref="later-observation",
                receipt_ref="receipt:later",
                result_fingerprint="sha256:later",
            )

    def test_unknown_never_retries_and_reconciliation_preserves_unknown_history(self):
        operation = self.hq.operations.prepare(
            idempotency_key="idem:unknown",
            intent=self.intent,
            approval=self.approval,
            song_id=self.song.id,
            version_id=self.version.id,
        )
        self.claim(operation.operation_id)
        unknown = self.hq.operations.mark_unknown(
            operation.operation_id,
            evidence_ref="transport-timeout:outcome-ambiguous",
        )
        self.assertEqual(unknown.recorded_state, "UNKNOWN")
        self.assertEqual(unknown.effective_outcome, "UNKNOWN")
        with self.assertRaises(DuplicateExecutionError):
            self.claim(operation.operation_id)

        reconciled = self.hq.operations.reconcile_unknown(
            operation.operation_id,
            observed_outcome="SUCCEEDED",
            evidence_ref="provider-status-query:confirmed",
            receipt_ref="provider-receipt:reconciled",
            result_fingerprint="sha256:reconciled-result",
        )
        self.assertEqual(reconciled.recorded_state, "UNKNOWN")
        self.assertEqual(reconciled.effective_outcome, "SUCCEEDED")
        self.assertTrue(reconciled.reconciled)
        self.assertEqual(
            reconciled.reconciliation_evidence_ref,
            "provider-status-query:confirmed",
        )
        events = self.hq.operations.events(operation.operation_id)
        self.assertEqual(
            [event.event_type for event in events],
            [
                "PREPARED",
                "EXECUTION_CLAIMED",
                "UNKNOWN",
                "RECONCILED_SUCCEEDED",
            ],
        )
        self.assertEqual(events[2].evidence_ref, "transport-timeout:outcome-ambiguous")
        with self.assertRaises(OperationError):
            self.hq.operations.reconcile_unknown(
                operation.operation_id,
                observed_outcome="FAILED",
                evidence_ref="contradictory-second-reconciliation",
            )

    def test_unknown_can_be_reconciled_to_failure_without_inventing_receipt(self):
        operation = self.hq.operations.prepare(
            idempotency_key="idem:unknown-failed",
            intent=self.intent,
            approval=self.approval,
        )
        self.claim(operation.operation_id)
        self.hq.operations.mark_unknown(
            operation.operation_id,
            evidence_ref="process-crash-after-claim",
        )
        reconciled = self.hq.operations.reconcile_unknown(
            operation.operation_id,
            observed_outcome="FAILED",
            evidence_ref="host-inspection:no-change-observed",
        )
        self.assertEqual(reconciled.recorded_state, "UNKNOWN")
        self.assertEqual(reconciled.effective_outcome, "FAILED")
        self.assertIsNone(reconciled.receipt_ref)

    def test_terminal_and_reconciliation_evidence_cannot_be_blank(self):
        operation = self.prepare("idem:blank-evidence")
        self.claim(operation.operation_id)
        with self.assertRaises(ValidationError):
            self.hq.operations.complete_success(
                operation.operation_id,
                receipt_ref=" ",
                evidence_ref="evidence",
                result_fingerprint="sha256:result",
            )
        with self.assertRaises(ValidationError):
            self.hq.operations.mark_unknown(
                operation.operation_id,
                evidence_ref=" ",
            )

    def test_song_version_binding_cannot_cross_song(self):
        other = self.hq.store.create_song("Other Song")
        other_version = self.hq.store.create_version(other.id, label="other-v1")
        with self.assertRaises(ValidationError):
            self.hq.operations.prepare(
                idempotency_key="idem:cross-song",
                intent=self.intent,
                approval=self.approval,
                song_id=self.song.id,
                version_id=other_version.id,
            )

    def test_operation_identity_and_history_are_sql_immutable(self):
        operation = self.prepare("idem:immutable")
        with self.assertRaises(sqlite3.IntegrityError):
            self.hq.store._conn.execute(
                "UPDATE operations SET idempotency_key='changed' WHERE id=?",
                (operation.operation_id,),
            )
        self.hq.store._conn.rollback()
        event_id = self.hq.operations.events(operation.operation_id)[0].id
        with self.assertRaises(sqlite3.IntegrityError):
            self.hq.store._conn.execute(
                "DELETE FROM operation_events WHERE id=?", (event_id,)
            )
        self.hq.store._conn.rollback()

    def test_activity_records_operation_lifecycle_in_order(self):
        checkpoint = self.hq.activity.checkpoint()
        operation = self.prepare("idem:activity")
        self.claim(operation.operation_id)
        self.hq.operations.mark_unknown(
            operation.operation_id,
            evidence_ref="ambiguous:activity",
        )
        self.hq.operations.reconcile_unknown(
            operation.operation_id,
            observed_outcome="FAILED",
            evidence_ref="reconcile:activity",
        )
        events = self.hq.activity.for_song(
            self.song.id,
            after_sequence=checkpoint,
        )
        operation_events = [
            event.event_type
            for event in events
            if event.object_type == "OPERATION" and event.object_id == operation.operation_id
        ]
        self.assertEqual(
            operation_events,
            [
                "OPERATION_PREPARED",
                "OPERATION_EXECUTION_CLAIMED",
                "OPERATION_UNKNOWN",
                "OPERATION_RECONCILED_FAILED",
            ],
        )

    def test_reopen_rejects_tampered_operation_event_sequence(self):
        operation = self.prepare("idem:tamper")
        self.hq.store._conn.execute(
            "INSERT INTO operation_events(id,operation_id,event_type,evidence_ref) "
            "VALUES('opevt_tampered',?,'PREPARED','tamper')",
            (operation.operation_id,),
        )
        self.hq.store._conn.commit()
        self.hq.close()
        with self.assertRaises(LineageCorruptionError):
            HeadquartersMemory.open(self.root, self.profile_id)
        self.hq = HeadquartersMemory.open if False else self.hq


if __name__ == "__main__":
    unittest.main()
