import tempfile
import unittest

from n0te2.activity import ActivityLog
from n0te2.creative_suggestions import CreativeSuggestionService
from n0te2.evidence import EvidenceMemory
from n0te2.lineage import LineageStore
from n0te2.memory import HeadquartersMemory
from n0te2.session import SessionMemory
from n0te2.suggestion_deferral import (
    AFTER_RELEASE,
    LATER_THIS_SONG,
    NEVER_SUGGEST_AGAIN,
    NEXT_SONG,
    SOMEDAY,
    SuggestionDeferralMemory,
)


class SuggestionDeferralTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hq = HeadquartersMemory.create(self.tmp.name, "Deferral Artist")
        self.song = self.hq.store.create_song("Deferred Song")
        self.session = self.hq.sessions.start_session(
            song_id=self.song.id,
            objective="Keep the current idea focused",
        )
        self.suggestions = CreativeSuggestionService(
            self.hq.store, self.hq.sessions, self.hq.suggestion_deferrals
        )

    def tearDown(self):
        self.hq.close()

    def test_all_five_horizons_are_durable_and_append_only(self):
        for index, horizon in enumerate(
            (LATER_THIS_SONG, AFTER_RELEASE, NEXT_SONG, SOMEDAY, NEVER_SUGGEST_AGAIN)
        ):
            suggestion = CreativeSuggestionService(self.hq.store, self.hq.sessions).suggest(
                distance="ADJACENT", variation=index
            )
            record = self.hq.suggestion_deferrals.defer(suggestion.semantic_key, horizon)
            self.assertEqual(record.horizon, horizon)
            self.assertEqual(record.action, "DEFER")
        self.assertEqual(len(self.hq.suggestion_deferrals.history()), 5)
        profile_id = self.hq.store.profile_id
        self.hq.close()
        self.hq = HeadquartersMemory.open(self.tmp.name, profile_id)
        self.assertEqual(len(self.hq.suggestion_deferrals.history()), 5)
        self.assertEqual(len(self.hq.suggestion_deferrals.active_deferrals()), 5)

    def test_later_this_song_expires_when_work_session_changes(self):
        baseline = CreativeSuggestionService(self.hq.store, self.hq.sessions).suggest(distance="FAMILIAR")
        self.hq.suggestion_deferrals.defer(baseline.semantic_key, LATER_THIS_SONG)
        self.assertTrue(self.hq.suggestion_deferrals.applies(baseline.semantic_key))
        hidden = self.suggestions.suggest(distance="FAMILIAR")
        self.assertNotEqual(hidden.semantic_key, baseline.semantic_key)
        self.hq.sessions.close_session(
            self.session.id,
            debrief_summary="Paused this idea for now",
            next_action="Return with fresh ears",
        )
        later = self.hq.sessions.start_session(song_id=self.song.id, objective="Fresh pass")
        self.assertNotEqual(later.id, self.session.id)
        self.assertFalse(self.hq.suggestion_deferrals.applies(baseline.semantic_key))
        visible = self.suggestions.suggest(distance="FAMILIAR")
        clean = CreativeSuggestionService(self.hq.store, self.hq.sessions).suggest(distance="FAMILIAR")
        self.assertEqual(visible, clean)

    def test_next_song_only_suppresses_originating_song(self):
        baseline = CreativeSuggestionService(self.hq.store, self.hq.sessions).suggest(distance="ADJACENT")
        self.hq.suggestion_deferrals.defer(baseline.semantic_key, NEXT_SONG)
        self.assertTrue(self.hq.suggestion_deferrals.applies(baseline.semantic_key))
        other = self.hq.store.create_song("Next Song")
        self.hq.store.select_song(other.id)
        self.hq.sessions.start_session(song_id=other.id, objective="Different Song")
        self.assertFalse(self.hq.suggestion_deferrals.applies(baseline.semantic_key))

    def test_after_release_requires_exact_originating_song_release_evidence(self):
        baseline = CreativeSuggestionService(self.hq.store, self.hq.sessions).suggest(distance="WILDCARD")
        self.hq.suggestion_deferrals.defer(baseline.semantic_key, AFTER_RELEASE)
        self.assertTrue(self.hq.suggestion_deferrals.applies(baseline.semantic_key))
        unrelated = self.hq.store.create_song("Released Elsewhere")
        self.hq.store.select_song(self.song.id)
        self.assertTrue(
            self.hq.suggestion_deferrals.applies(
                baseline.semantic_key, released_song_ids={unrelated.id}
            )
        )
        self.assertFalse(
            self.hq.suggestion_deferrals.applies(
                baseline.semantic_key, released_song_ids={self.song.id}
            )
        )

    def test_someday_and_never_are_artist_scoped_but_reversible(self):
        first = CreativeSuggestionService(self.hq.store, self.hq.sessions).suggest(distance="ADJACENT")
        someday = self.hq.suggestion_deferrals.defer(first.semantic_key, SOMEDAY)
        second = CreativeSuggestionService(self.hq.store, self.hq.sessions).suggest(distance="WILDCARD")
        never = self.hq.suggestion_deferrals.defer(second.semantic_key, NEVER_SUGGEST_AGAIN)
        other = self.hq.store.create_song("Other Song")
        self.hq.store.select_song(other.id)
        self.hq.sessions.start_session(song_id=other.id, objective="Other work")
        self.assertTrue(self.hq.suggestion_deferrals.applies(first.semantic_key))
        self.assertTrue(self.hq.suggestion_deferrals.applies(second.semantic_key))
        self.hq.suggestion_deferrals.restore(someday.deferral_id)
        self.hq.suggestion_deferrals.restore(never.deferral_id)
        self.assertFalse(self.hq.suggestion_deferrals.applies(first.semantic_key))
        self.assertFalse(self.hq.suggestion_deferrals.applies(second.semantic_key))
        history = self.hq.suggestion_deferrals.history()
        self.assertEqual([item.action for item in history[-2:]], ["RESTORE", "RESTORE"])

    def test_duplicate_defer_and_restore_are_idempotent_and_audited_once_each(self):
        suggestion = CreativeSuggestionService(self.hq.store, self.hq.sessions).suggest(distance="ADJACENT")
        before = len(self.hq.activity.for_profile())
        first = self.hq.suggestion_deferrals.defer(suggestion.semantic_key, SOMEDAY)
        second = self.hq.suggestion_deferrals.defer(suggestion.semantic_key, SOMEDAY)
        self.assertEqual(first, second)
        restored = self.hq.suggestion_deferrals.restore(first.deferral_id)
        replay = self.hq.suggestion_deferrals.restore(first.deferral_id)
        self.assertEqual(restored, replay)
        events = self.hq.activity.for_profile()[before:]
        self.assertEqual(
            [item.event_type for item in events],
            ["SUGGESTION_DEFERRED", "SUGGESTION_DEFERRAL_CLEARED"],
        )

    def test_v1_later_this_song_rows_migrate_losslessly_without_replaying_activity(self):
        root = tempfile.mkdtemp(dir=self.tmp.name)
        store = LineageStore.create(root, "Legacy Deferral Artist")
        evidence = EvidenceMemory(store)
        activity = ActivityLog(store)
        sessions = SessionMemory(store, evidence)
        song = store.create_song("Legacy Song")
        session = sessions.start_session(song_id=song.id, objective="Legacy work")
        legacy_id = "defer_legacy"
        with store._tx():
            store._conn.execute(
                "CREATE TABLE suggestion_deferrals ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,"
                "artist_id TEXT NOT NULL REFERENCES artists(id),"
                "song_id TEXT NOT NULL REFERENCES songs(id),"
                "session_id TEXT NOT NULL REFERENCES sessions(id),"
                "semantic_key TEXT NOT NULL,"
                "scope TEXT NOT NULL CHECK(scope='LATER_THIS_SONG'),"
                "UNIQUE(artist_id,song_id,session_id,semantic_key,scope))"
            )
            store._conn.execute(
                "INSERT INTO suggestion_deferrals(id,artist_id,song_id,session_id,semantic_key,scope) "
                "VALUES(?,?,?,?,?,?)",
                (legacy_id, store.primary_artist_id, song.id, session.id, "melody:motif-variation", LATER_THIS_SONG),
            )
            # Represent the Activity event already emitted when the real v1 artist
            # action originally happened. Migration must not emit it again.
            store._conn.execute(
                "INSERT INTO activity_events("
                "id,event_type,artist_id,song_id,version_id,object_type,object_id,payload_json"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    "act_legacy_deferral",
                    "SUGGESTION_DEFERRED",
                    store.primary_artist_id,
                    song.id,
                    None,
                    "SUGGESTION_DEFERRAL",
                    legacy_id,
                    '{"scope":"LATER_THIS_SONG"}',
                ),
            )
            store._conn.execute(
                "INSERT INTO metadata(key,value) VALUES('suggestion_deferral_schema_version','1')"
            )
        activity_before = len(activity.for_profile())
        migrated = SuggestionDeferralMemory(store, sessions)
        history = migrated.history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].deferral_id, legacy_id)
        self.assertEqual(history[0].horizon, LATER_THIS_SONG)
        self.assertTrue(migrated.applies("melody:motif-variation"))
        self.assertEqual(len(activity.for_profile()), activity_before)
        self.assertEqual(
            store._conn.execute(
                "SELECT value FROM metadata WHERE key='suggestion_deferral_schema_version'"
            ).fetchone()[0],
            "2",
        )
        store.close()


if __name__ == "__main__":
    unittest.main()
