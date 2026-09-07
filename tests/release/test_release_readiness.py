import sqlite3
import tempfile
import unittest
from pathlib import Path

from n0te2.activity import ActivityLog
from n0te2.evidence import EvidenceMemory
from n0te2.lineage import LineageCorruptionError, LineageStore, ValidationError
from n0te2.release_readiness import (
    ReleaseReadinessMemory,
    StaleReleasePlanError,
)


class ReleaseReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = LineageStore.create(self.root, "Artist")
        self.evidence = EvidenceMemory(self.store)
        self.activity = ActivityLog(self.store)
        self.profile_id = self.store.profile_id
        self.song = self.store.create_song("Release Song")
        self.v1 = self.store.create_version(self.song.id, label="release candidate")

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def _approve(self):
        self.store.approve_version(self.song.id, self.v1.id)

    def test_backward_plan_reaches_review_ready_and_survives_relaunch(self):
        self._approve()
        memory = ReleaseReadinessMemory(self.store)
        plan = memory.create_plan(self.song.id, target_on="2026-10-31")

        empty = memory.snapshot(plan.id)
        self.assertEqual(empty.approved_version_state, "PRESENT")
        self.assertEqual(empty.review_state, "UNKNOWN")
        self.assertTrue(any("No required release deliverables" in item for item in empty.unresolved))
        self.assertFalse(empty.external_action_authority_granted)
        self.assertFalse(empty.distribution_uploaded)

        master = memory.add_deliverable(
            memory.plan_binding(plan.id),
            kind="MASTER_FILE",
            label="Final master file",
            required=True,
            state="READY",
            note="Artist records the local master asset as ready for release prep.",
        )
        artwork = memory.add_deliverable(
            memory.plan_binding(plan.id),
            kind="COVER_ART",
            label="Cover artwork",
            required=True,
            state="MISSING",
        )
        milestone = memory.add_milestone(
            memory.plan_binding(plan.id),
            label="Lock release package",
            lead_days=14,
            state="OPEN",
        )
        self.assertEqual(milestone.due_on, "2026-10-17")

        missing = memory.snapshot(plan.id)
        self.assertEqual(missing.review_state, "MISSING")
        self.assertEqual(master.state, "READY")

        memory.set_deliverable_state(
            memory.deliverable_binding(artwork.id),
            state="READY",
            note="Artist records artwork as ready locally.",
        )
        memory.set_milestone_state(
            memory.milestone_binding(milestone.id),
            state="DONE",
            note="Release package prep milestone completed locally.",
        )
        ready = memory.snapshot(plan.id)
        self.assertEqual(ready.review_state, "READY_FOR_REVIEW")
        self.assertEqual(ready.unresolved, ())
        self.assertEqual(ready.approved_version_id, self.v1.id)
        self.assertFalse(ready.provider_release_scheduled)
        self.assertFalse(ready.pitch_submitted)
        self.assertFalse(ready.spend_authorized)
        self.assertFalse(ready.legal_clearance_verified)

        self.store.close()
        self.store = LineageStore.open(self.root, self.profile_id)
        reopened = ReleaseReadinessMemory(self.store)
        after = reopened.snapshot(plan.id)
        self.assertEqual(after.review_state, "READY_FOR_REVIEW")
        self.assertEqual(after.milestones[0].due_on, "2026-10-17")
        self.assertEqual(after.deliverables[0].state, "READY")
        self.assertEqual(after.deliverables[1].state, "READY")

    def test_required_deliverable_cannot_be_erased_as_not_required(self):
        memory = ReleaseReadinessMemory(self.store)
        plan = memory.create_plan(self.song.id, target_on="2026-11-20")

        with self.assertRaises(ValidationError):
            memory.add_deliverable(
                memory.plan_binding(plan.id),
                kind="METADATA",
                label="Release metadata",
                required=True,
                state="NOT_REQUIRED",
            )

        required = memory.add_deliverable(
            memory.plan_binding(plan.id),
            kind="METADATA",
            label="Release metadata",
            required=True,
            state="UNKNOWN",
        )
        with self.assertRaises(ValidationError):
            memory.set_deliverable_state(
                memory.deliverable_binding(required.id),
                state="NOT_REQUIRED",
            )

        optional = memory.add_deliverable(
            memory.plan_binding(plan.id),
            kind="PITCH_ASSET",
            label="Optional pitch asset",
            required=False,
            state="NOT_REQUIRED",
        )
        self.assertEqual(optional.state, "NOT_REQUIRED")

        with self.assertRaises(sqlite3.IntegrityError):
            self.store._conn.execute(
                "INSERT INTO release_deliverable_events(id,deliverable_id,state,note) "
                "VALUES('reldevt_forced',?,'NOT_REQUIRED',NULL)",
                (required.id,),
            )

    def test_stale_plan_and_item_bindings_fail_after_any_plan_revision(self):
        memory = ReleaseReadinessMemory(self.store)
        plan = memory.create_plan(self.song.id, target_on="2026-12-01")
        first_view = memory.plan_binding(plan.id)

        item = memory.add_deliverable(
            first_view,
            kind="MASTER_FILE",
            label="Master",
            required=True,
        )
        with self.assertRaises(StaleReleasePlanError):
            memory.add_milestone(
                first_view,
                label="Old rendered form",
                lead_days=10,
            )

        item_view = memory.deliverable_binding(item.id)
        memory.add_milestone(
            memory.plan_binding(plan.id),
            label="Artwork lock",
            lead_days=21,
        )
        with self.assertRaises(StaleReleasePlanError):
            memory.set_deliverable_state(item_view, state="READY")

    def test_archiving_freezes_current_readiness_and_new_target_is_new_lineage(self):
        memory = ReleaseReadinessMemory(self.store)
        first = memory.create_plan(self.song.id, target_on="2026-10-01")
        item = memory.add_deliverable(
            memory.plan_binding(first.id),
            kind="MASTER_FILE",
            label="Master",
            required=True,
        )
        item_binding = memory.deliverable_binding(item.id)
        archived = memory.archive_plan(
            memory.plan_binding(first.id),
            note="Artist moved the intended release window.",
        )
        self.assertEqual(archived.state, "ARCHIVED")
        with self.assertRaises(StaleReleasePlanError):
            memory.snapshot(first.id)
        with self.assertRaises(StaleReleasePlanError):
            memory.set_deliverable_state(item_binding, state="READY")

        second = memory.create_plan(self.song.id, target_on="2026-11-15")
        self.assertNotEqual(first.id, second.id)
        history = memory.plan_history(self.song.id)
        self.assertEqual([entry.id for entry in history], [first.id, second.id])
        self.assertEqual(history[0].target_on, "2026-10-01")
        self.assertEqual(history[1].target_on, "2026-11-15")
        self.assertEqual(memory.active_plan_for_song(self.song.id).id, second.id)

    def test_approved_version_is_only_a_prerequisite_not_delivery(self):
        memory = ReleaseReadinessMemory(self.store)
        plan = memory.create_plan(self.song.id, target_on="2027-01-10")
        memory.add_deliverable(
            memory.plan_binding(plan.id),
            kind="MASTER_FILE",
            label="Delivered master",
            required=True,
            state="MISSING",
        )

        before = memory.snapshot(plan.id)
        self.assertEqual(before.approved_version_state, "MISSING")
        self.assertEqual(before.review_state, "MISSING")

        self._approve()
        after = memory.snapshot(plan.id)
        self.assertEqual(after.approved_version_state, "PRESENT")
        self.assertEqual(after.review_state, "MISSING")
        self.assertIn("Delivered master: MISSING", after.unresolved)

    def test_input_boundaries_reject_lossy_or_invented_timing(self):
        memory = ReleaseReadinessMemory(self.store)
        with self.assertRaises(ValidationError):
            memory.create_plan(self.song.id, target_on="next month")
        with self.assertRaises(ValidationError):
            memory.create_plan(self.song.id, target_on=20261101)

        plan = memory.create_plan(self.song.id, target_on="2026-11-01")
        for value in (10.5, "10", True, -1, 731):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    memory.add_milestone(
                        memory.plan_binding(plan.id),
                        label="Explicit lead time",
                        lead_days=value,
                    )

        with self.assertRaises(ValidationError):
            memory.add_deliverable(
                memory.plan_binding(plan.id),
                kind="MASTER_FILE",
                label="Master",
                required=1,
            )

    def test_missing_integrity_trigger_fails_closed_on_reopen(self):
        memory = ReleaseReadinessMemory(self.store)
        memory.create_plan(self.song.id, target_on="2026-09-30")
        self.store._conn.execute("DROP TRIGGER release_deliverable_event_requirement")
        self.store._conn.commit()
        self.store.close()

        self.store = LineageStore.open(self.root, self.profile_id)
        with self.assertRaises(LineageCorruptionError):
            ReleaseReadinessMemory(self.store)


if __name__ == "__main__":
    unittest.main()
