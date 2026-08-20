import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from n0te2 import (
    EvidenceMemory,
    HeadquartersMemory,
    LineageCorruptionError,
    LineageStore,
    RecoveryManager,
)


V1_EVIDENCE_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE evidence_claims (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('PROFILE','ARTIST','SONG','VERSION')),
    scope_id TEXT NOT NULL,
    key TEXT NOT NULL CHECK(length(trim(key)) > 0),
    value_json TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('USER_DECLARED','OBSERVED','MEASURED','PROVIDER_VERIFIED','REMEMBERED','INFERRED')),
    source_ref TEXT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0)
);
CREATE TABLE evidence_supersessions (
    new_claim_id TEXT NOT NULL REFERENCES evidence_claims(id),
    old_claim_id TEXT NOT NULL REFERENCES evidence_claims(id),
    PRIMARY KEY(new_claim_id, old_claim_id),
    CHECK(new_claim_id <> old_claim_id)
);
CREATE INDEX evidence_claim_lookup
ON evidence_claims(scope_kind, scope_id, key, seq);
CREATE INDEX evidence_superseded_lookup
ON evidence_supersessions(old_claim_id);
INSERT INTO metadata(key, value) VALUES('evidence_schema_version', '1');
COMMIT;
"""


class Core01JCrashConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_failed_initializer_rolls_back_every_schema_object(self):
        db = self.root / "partial.sqlite3"
        conn = LineageStore._connect(db)

        def deny_second_table(action, arg1, arg2, db_name, trigger_name):
            if action == sqlite3.SQLITE_CREATE_TABLE and arg1 == "artists":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(deny_second_table)
        with self.assertRaises(sqlite3.DatabaseError):
            LineageStore._initialize(
                conn,
                "prf_" + "1" * 32,
                "Atomic Artist",
            )
        conn.set_authorizer(None)
        objects = conn.execute(
            "SELECT type,name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        self.assertEqual(objects, [])
        self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
        conn.close()

    def test_process_kill_before_commit_preserves_last_committed_song_state(self):
        store = LineageStore.create(self.root, "Artist")
        profile = store.profile_id
        prior = store.create_song("Committed Song")
        prior_count = store._conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
        store.close()

        repo = Path(__file__).resolve().parents[2]
        child = textwrap.dedent(
            """
            import os
            import sys
            from n0te2 import LineageStore

            root, profile = sys.argv[1], sys.argv[2]
            store = LineageStore.open(root, profile)
            store._conn.execute('BEGIN IMMEDIATE')
            store._conn.execute(
                'INSERT INTO songs(id,artist_id,title) VALUES(?,?,?)',
                ('song_' + 'f' * 32, store.primary_artist_id, 'UNCOMMITTED')
            )
            store._conn.execute(
                "UPDATE metadata SET value=? WHERE key='active_song_id'",
                ('song_' + 'f' * 32,)
            )
            os._exit(23)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", child, str(self.root), profile],
            cwd=repo,
            env={**os.environ, "PYTHONPATH": str(repo)},
            check=False,
        )
        self.assertEqual(result.returncode, 23)

        reopened = LineageStore.open(self.root, profile)
        self.addCleanup(reopened.close)
        self.assertEqual(
            reopened._conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0],
            prior_count,
        )
        self.assertIsNone(reopened.get_song("song_" + "f" * 32))
        self.assertEqual(reopened.active_song().id, prior.id)
        self.assertEqual(reopened._conn.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_sqlite_full_rolls_back_large_song_write_and_preserves_integrity(self):
        store = LineageStore.create(self.root, "Artist")
        profile = store.profile_id
        prior = store.create_song("Prior")
        prior_count = store._conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
        page_count = int(store._conn.execute("PRAGMA page_count").fetchone()[0])
        store._conn.execute(f"PRAGMA max_page_count={page_count}")

        with self.assertRaises(sqlite3.OperationalError):
            store.create_song("x" * 2_000_000)

        store._conn.execute("PRAGMA max_page_count=2147483646")
        self.assertEqual(
            store._conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0],
            prior_count,
        )
        self.assertEqual(store.active_song().id, prior.id)
        self.assertEqual(store._conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
        store.close()

        reopened = LineageStore.open(self.root, profile)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.active_song().id, prior.id)
        self.assertEqual(
            reopened._conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0],
            prior_count,
        )

    def test_interrupted_evidence_migration_rolls_back_and_retry_succeeds(self):
        store = LineageStore.create(self.root, "Artist")
        self.addCleanup(store.close)
        song = store.create_song("Song")
        store._conn.executescript(V1_EVIDENCE_SCHEMA)
        store._conn.execute(
            "INSERT INTO evidence_claims("
            "id,scope_kind,scope_id,key,value_json,source_kind,source_ref,confidence"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (
                "claim_" + "1" * 32,
                "SONG",
                song.id,
                "creative.note",
                '"preserve me"',
                "USER_DECLARED",
                "migration:test",
                0.9,
            ),
        )
        store._conn.commit()

        def deny_metadata_update(action, arg1, arg2, db_name, trigger_name):
            if action == sqlite3.SQLITE_UPDATE and arg1 == "metadata":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        store._conn.set_authorizer(deny_metadata_update)
        with self.assertRaises(LineageCorruptionError):
            EvidenceMemory(store)
        store._conn.set_authorizer(None)

        version = store._conn.execute(
            "SELECT value FROM metadata WHERE key='evidence_schema_version'"
        ).fetchone()[0]
        columns = {
            row[1] for row in store._conn.execute("PRAGMA table_info(evidence_claims)")
        }
        self.assertEqual(version, "1")
        self.assertNotIn("twin_domain", columns)
        self.assertEqual(
            store._conn.execute("SELECT COUNT(*) FROM evidence_claims").fetchone()[0],
            1,
        )

        memory = EvidenceMemory(store)
        migrated = memory.get_claim("claim_" + "1" * 32)
        self.assertEqual(migrated.value, "preserve me")
        self.assertEqual(migrated.source_ref, "migration:test")
        self.assertEqual(migrated.twin_domain, "UNSPECIFIED")
        self.assertEqual(
            store._conn.execute(
                "SELECT value FROM metadata WHERE key='evidence_schema_version'"
            ).fetchone()[0],
            "2",
        )

    def test_corrupt_live_database_requires_explicit_verified_snapshot_restore(self):
        hq = HeadquartersMemory.create(self.root, "Artist")
        profile = hq.store.profile_id
        song = hq.store.create_song("Song")
        v1 = hq.store.create_version(song.id, label="v1")
        hq.store.approve_version(song.id, v1.id)
        snapshot = hq.recovery.create_snapshot()
        live = hq.store.database_path
        hq.close()

        live.write_bytes(b"corrupt live state")
        with self.assertRaises(LineageCorruptionError):
            HeadquartersMemory.open(self.root, profile)
        self.assertEqual(live.read_bytes(), b"corrupt live state")

        result = RecoveryManager.restore_snapshot(
            self.root,
            profile,
            expected_sha256=snapshot.sha256,
        )
        self.assertEqual(result.installed_sha256, snapshot.sha256)
        self.assertIsNotNone(result.preserved_database)
        self.assertEqual(result.preserved_database.read_bytes(), b"corrupt live state")

        restored = HeadquartersMemory.open(self.root, profile)
        self.addCleanup(restored.close)
        state = restored.store.get_song(song.id)
        self.assertEqual(state.current_version_id, v1.id)
        self.assertEqual(state.approved_version_id, v1.id)
        self.assertEqual(restored.store._conn.execute("PRAGMA quick_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
