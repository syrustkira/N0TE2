import tempfile
import unittest

from n0te2.creative_suggestions import (
    CREATIVE_DIMENSIONS,
    CreativeSuggestionError,
    CreativeSuggestionService,
)
from n0te2.memory import HeadquartersMemory


class CreativeSuggestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hq = HeadquartersMemory.create(self.tmp.name, "Suggestion Artist")
        self.addCleanup(self.hq.close)
        self.song = self.hq.store.create_song("Suggestion Song")
        self.session = self.hq.sessions.start_session(
            song_id=self.song.id,
            objective="Find a stronger lift into the chorus without rebuilding the whole Song",
        )
        self.service = CreativeSuggestionService(self.hq.store, self.hq.sessions)
        self.attentive = CreativeSuggestionService(
            self.hq.store, self.hq.sessions, self.hq.suggestion_deferrals
        )

    def _counts(self):
        conn = self.hq.store._conn
        tables = (
            "songs",
            "sessions",
            "session_items",
            "evidence_claims",
            "activity_events",
            "attention_focus_sessions",
            "operations",
        )
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }

    def test_same_request_is_deterministic_and_local(self):
        first = self.service.suggest(distance="adjacent", locked_dimensions=("MELODY",))
        second = self.service.suggest(distance="ADJACENT", locked_dimensions=("MELODY",))
        self.assertEqual(first, second)
        self.assertEqual(first.source_kind, "DETERMINISTIC_LOCAL")
        self.assertFalse(first.personalized)
        self.assertFalse(first.provider_used)
        self.assertFalse(first.action_authority_granted)
        self.assertEqual(first.song_title, "Suggestion Song")
        self.assertEqual(first.session_id, self.session.id)
        self.assertEqual(first.session_objective, self.session.objective)

    def test_distance_modes_change_semantics_without_claiming_taste(self):
        results = {
            mode: self.service.suggest(distance=mode, variation=5)
            for mode in ("FAMILIAR", "ADJACENT", "WILDCARD")
        }
        self.assertIn("does not claim to know your personal taste", results["FAMILIAR"].distance_explanation)
        self.assertIn("one unlocked creative dimension", results["ADJACENT"].distance_explanation)
        self.assertIn("larger deliberate contrast", results["WILDCARD"].distance_explanation)
        self.assertEqual({item.distance for item in results.values()}, {"FAMILIAR", "ADJACENT", "WILDCARD"})

    def test_locked_dimensions_are_never_selected(self):
        for allowed in CREATIVE_DIMENSIONS:
            locked = tuple(item for item in CREATIVE_DIMENSIONS if item != allowed)
            result = self.service.suggest(
                distance="WILDCARD",
                locked_dimensions=locked,
                variation=17,
            )
            self.assertEqual(result.dimension, allowed)

    def test_all_dimensions_locked_fails_closed(self):
        with self.assertRaisesRegex(CreativeSuggestionError, "Every creative dimension is locked"):
            self.service.suggest(distance="FAMILIAR", locked_dimensions=CREATIVE_DIMENSIONS)

    def test_suggestion_reads_do_not_mutate_canonical_state(self):
        before = self._counts()
        for mode in ("FAMILIAR", "ADJACENT", "WILDCARD"):
            self.service.suggest(distance=mode, variation=3)
        self.assertEqual(self._counts(), before)

    def test_suggestion_requires_an_active_song(self):
        other_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(other_tmp.cleanup)
        empty = HeadquartersMemory.create(other_tmp.name, "No Song Artist")
        self.addCleanup(empty.close)
        service = CreativeSuggestionService(empty.store, empty.sessions)
        with self.assertRaisesRegex(CreativeSuggestionError, "Start or select a Song"):
            service.suggest(distance="FAMILIAR")

    def test_real_semantic_deferral_changes_service_selection(self):
        baseline = self.service.suggest(distance="ADJACENT")
        first = self.attentive.suggest(distance="ADJACENT")
        self.assertEqual(first, baseline)
        self.hq.suggestion_deferrals.defer(first.semantic_key, "LATER_THIS_SONG")
        second = self.attentive.suggest(distance="ADJACENT")
        self.assertNotEqual(second.semantic_key, first.semantic_key)

    def test_after_release_only_expires_for_exact_song_release_evidence(self):
        first = self.attentive.suggest(distance="WILDCARD")
        self.hq.suggestion_deferrals.defer(first.semantic_key, "AFTER_RELEASE")
        hidden = self.attentive.suggest(distance="WILDCARD")
        self.assertNotEqual(hidden.semantic_key, first.semantic_key)
        unrelated = self.hq.store.create_song("Released Elsewhere")
        self.hq.store.select_song(self.song.id)
        still_hidden = self.attentive.suggest(
            distance="WILDCARD", released_song_ids={unrelated.id}
        )
        self.assertNotEqual(still_hidden.semantic_key, first.semantic_key)
        released = self.attentive.suggest(
            distance="WILDCARD", released_song_ids={self.song.id}
        )
        self.assertEqual(released.semantic_key, first.semantic_key)

    def test_all_available_suggestions_deferred_fails_closed(self):
        for allowed in CREATIVE_DIMENSIONS:
            locked = tuple(item for item in CREATIVE_DIMENSIONS if item != allowed)
            result = self.attentive.suggest(distance="ADJACENT", locked_dimensions=locked)
            self.hq.suggestion_deferrals.defer(result.semantic_key, "NEVER_SUGGEST_AGAIN")
        with self.assertRaisesRegex(CreativeSuggestionError, "Every available suggestion is deferred"):
            self.attentive.suggest(distance="ADJACENT")


if __name__ == "__main__":
    unittest.main()
