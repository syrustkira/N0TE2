from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from n0te2 import HeadquartersMemory
from n0te2.interaction_depth import (
    INTERACTION_DEPTH_MODES,
    InteractionDepthService,
    StaleInteractionDepthError,
)


def seed_episode(hq: HeadquartersMemory):
    song = hq.store.create_song("Interaction Song")
    session = hq.sessions.start_session(song_id=song.id, objective="Improve the chorus")
    episode = hq.learning.create_episode(
        session_id=session.id,
        domain="ARRANGEMENT",
        subject_ref="chorus impact",
        change_description="Mute the pre-chorus kick for one bar before the chorus",
    )
    return song, session, episode


class InteractionDepthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.hq = HeadquartersMemory.create(self.root, "Interaction Artist")
        self.song, self.session, self.episode = seed_episode(self.hq)
        self.service = InteractionDepthService(self.hq.learning)

    def tearDown(self) -> None:
        self.hq.close()
        self.tmp.cleanup()

    def test_all_five_modes_are_distinct_and_never_grant_authority(self) -> None:
        binding = self.service.binding_for(self.episode.id)
        plans = [self.service.plan(binding, mode) for mode in INTERACTION_DEPTH_MODES]
        self.assertEqual({plan.mode for plan in plans}, set(INTERACTION_DEPTH_MODES))
        self.assertEqual(len({plan.n0te_role for plan in plans}), 5)
        self.assertEqual(len({plan.artist_role for plan in plans}), 5)
        self.assertEqual(len({plan.next_step for plan in plans}), 5)
        self.assertTrue(all(plan.action_authority_granted is False for plan in plans))
        self.assertTrue(all(plan.mutation_permitted_by_mode is False for plan in plans))
        self.assertEqual(
            [plan.mode for plan in plans if plan.execution_requested],
            ["DO_IT"],
        )

    def test_do_it_requests_leadership_without_claiming_execution(self) -> None:
        plan = self.service.plan(self.service.binding_for(self.episode.id), "DO IT")
        self.assertEqual(plan.mode, "DO_IT")
        self.assertTrue(plan.execution_requested)
        self.assertFalse(plan.action_authority_granted)
        self.assertFalse(plan.mutation_permitted_by_mode)
        self.assertIn("does not itself bind an executor", plan.next_step)
        self.assertIn("verified execution surface", plan.next_step)

    def test_let_me_try_keeps_n0te_out_of_mutation(self) -> None:
        plan = self.service.plan(self.service.binding_for(self.episode.id), "LET ME TRY")
        self.assertEqual(plan.mode, "LET_ME_TRY")
        self.assertFalse(plan.execution_requested)
        self.assertIn("Stand back", plan.n0te_role)
        self.assertIn("yourself", plan.artist_role)
        self.assertIn("should not mutate", plan.next_step)

    def test_planning_is_pure_and_does_not_create_learning_or_skill_evidence(self) -> None:
        before_changes = self.hq.store._conn.total_changes
        before_episode = self.hq.learning.get_episode(self.episode.id)
        before_skills = self.hq.skills.states()
        binding = self.service.binding_for(self.episode.id)
        for mode in INTERACTION_DEPTH_MODES:
            self.service.plan(binding, mode)
        self.assertEqual(self.hq.store._conn.total_changes, before_changes)
        self.assertEqual(self.hq.learning.get_episode(self.episode.id), before_episode)
        self.assertEqual(self.hq.skills.states(), before_skills)

    def test_binding_fails_closed_when_learning_evidence_changes(self) -> None:
        binding = self.service.binding_for(self.episode.id)
        self.hq.learning.append_consequence(
            self.episode.id,
            observation="The chorus felt larger",
            source_kind="USER_DECLARED",
            source_ref="test:observation",
            confidence=0.7,
        )
        with self.assertRaises(StaleInteractionDepthError):
            self.service.plan(binding, "WITH_ME")

    def test_binding_fails_closed_when_active_song_changes(self) -> None:
        binding = self.service.binding_for(self.episode.id)
        other = self.hq.store.create_song("Other Song")
        self.hq.store.select_song(other.id)
        with self.assertRaises(StaleInteractionDepthError):
            self.service.plan(binding, "EXPLAIN_WHY")

    def test_current_binding_uses_latest_open_learning_job_only(self) -> None:
        current = self.service.current_binding()
        self.assertIsNotNone(current)
        self.assertEqual(current.episode_id, self.episode.id)
        self.hq.learning.decide(
            self.episode.id,
            decision="INCONCLUSIVE",
            rationale="Need more evidence",
            confidence=0.5,
        )
        self.assertIsNone(self.service.current_binding())


if __name__ == "__main__":
    unittest.main()
