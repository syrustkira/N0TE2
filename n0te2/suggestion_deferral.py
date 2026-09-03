from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from .lineage import LineageCorruptionError, LineageStore, ValidationError
from .session import SessionMemory

SUGGESTION_DEFERRAL_SCHEMA_VERSION = 1
LATER_THIS_SONG = "LATER_THIS_SONG"


@dataclass(frozen=True)
class SuggestionDeferral:
    sequence: int
    id: str
    artist_id: str
    song_id: str
    session_id: str
    semantic_key: str
    scope: str


class SuggestionDeferralMemory:
    """Explicit artist-owned suggestion deferrals in the canonical profile DB.

    The first bounded contract is LATER_THIS_SONG: a suggestion key is hidden
    for the exact Song work Session in which the artist deferred it. The record
    remains durable history, while a distinct later Session naturally makes the
    key eligible again without mutating or deleting the original decision.
    """

    _TRIGGERS = {
        "suggestion_deferral_binding_valid",
        "suggestion_deferral_immutable",
        "suggestion_deferral_delete_immutable",
        "suggestion_deferral_activity",
    }

    def __init__(self, store: LineageStore, sessions: SessionMemory):
        if not isinstance(store, LineageStore):
            raise TypeError("SuggestionDeferralMemory requires LineageStore")
        if not isinstance(sessions, SessionMemory) or sessions.store is not store:
            raise TypeError("SuggestionDeferralMemory requires SessionMemory for the same LineageStore")
        self.store = store
        self.sessions = sessions
        self._conn = store._conn
        self._ensure_schema()
        self._validate_existing()

    def _table_exists(self, name: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def _metadata_value(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def _ensure_schema(self) -> None:
        exists = self._table_exists("suggestion_deferrals")
        version = self._metadata_value("suggestion_deferral_schema_version")
        if exists or version is not None:
            if not exists or version != str(SUGGESTION_DEFERRAL_SCHEMA_VERSION):
                raise LineageCorruptionError("Suggestion deferral schema metadata/table mismatch")
            return
        if not self._table_exists("sessions") or not self._table_exists("activity_events"):
            raise LineageCorruptionError("Suggestion deferral requires canonical Session and Activity memory first")
        try:
            with self.store._tx():
                self._conn.execute(
                    """CREATE TABLE suggestion_deferrals (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        artist_id TEXT NOT NULL REFERENCES artists(id),
                        song_id TEXT NOT NULL REFERENCES songs(id),
                        session_id TEXT NOT NULL REFERENCES sessions(id),
                        semantic_key TEXT NOT NULL,
                        scope TEXT NOT NULL CHECK(scope='LATER_THIS_SONG'),
                        UNIQUE(artist_id,song_id,session_id,semantic_key,scope)
                    )"""
                )
                for statement in self._trigger_statements():
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT INTO metadata(key,value) VALUES('suggestion_deferral_schema_version',?)",
                    (str(SUGGESTION_DEFERRAL_SCHEMA_VERSION),),
                )
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError("cannot initialize suggestion deferral memory") from exc

    @staticmethod
    def _trigger_statements() -> tuple[str, ...]:
        return (
            """CREATE TRIGGER suggestion_deferral_binding_valid
            BEFORE INSERT ON suggestion_deferrals
            WHEN NOT EXISTS (
                SELECT 1 FROM songs s
                JOIN sessions x ON x.song_id=s.id
                WHERE s.id=NEW.song_id
                  AND s.artist_id=NEW.artist_id
                  AND x.id=NEW.session_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'Suggestion deferral binding is invalid');
            END""",
            """CREATE TRIGGER suggestion_deferral_immutable
            BEFORE UPDATE ON suggestion_deferrals
            BEGIN
                SELECT RAISE(ABORT, 'Suggestion deferral history is immutable');
            END""",
            """CREATE TRIGGER suggestion_deferral_delete_immutable
            BEFORE DELETE ON suggestion_deferrals
            BEGIN
                SELECT RAISE(ABORT, 'Suggestion deferral history is immutable');
            END""",
            """CREATE TRIGGER suggestion_deferral_activity
            AFTER INSERT ON suggestion_deferrals
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,
                    object_type,object_id,payload_json
                ) VALUES(
                    'act_'||lower(hex(randomblob(16))),
                    'SUGGESTION_DEFERRED',NEW.artist_id,NEW.song_id,NULL,
                    'SUGGESTION_DEFERRAL',NEW.id,
                    '{\"scope\":\"LATER_THIS_SONG\"}'
                );
            END""",
        )

    @staticmethod
    def _normalize_key(value: str) -> str:
        key = str(value).strip()
        if not key or len(key) > 200:
            raise ValidationError("suggestion semantic key must be 1 through 200 characters")
        return key

    @staticmethod
    def _record(row: sqlite3.Row) -> SuggestionDeferral:
        return SuggestionDeferral(
            sequence=int(row["seq"]),
            id=str(row["id"]),
            artist_id=str(row["artist_id"]),
            song_id=str(row["song_id"]),
            session_id=str(row["session_id"]),
            semantic_key=str(row["semantic_key"]),
            scope=str(row["scope"]),
        )

    def _validate_existing(self) -> None:
        if self._metadata_value("suggestion_deferral_schema_version") != str(SUGGESTION_DEFERRAL_SCHEMA_VERSION):
            raise LineageCorruptionError("unsupported suggestion deferral schema version")
        triggers = {
            str(row["name"])
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'suggestion_deferral_%'"
            )
        }
        missing = self._TRIGGERS - triggers
        if missing:
            raise LineageCorruptionError(f"Suggestion deferral integrity hooks are incomplete: {sorted(missing)}")
        for row in self._conn.execute(
            "SELECT seq,id,artist_id,song_id,session_id,semantic_key,scope FROM suggestion_deferrals ORDER BY seq"
        ):
            record = self._record(row)
            if record.artist_id != self.store.primary_artist_id or record.scope != LATER_THIS_SONG:
                raise LineageCorruptionError("Suggestion deferral contains invalid profile or scope")
            song = self.store.get_song(record.song_id)
            if song is None or song.artist_id != record.artist_id:
                raise LineageCorruptionError("Suggestion deferral references invalid Song")

    def defer_later_this_song(self, semantic_key: str) -> SuggestionDeferral:
        key = self._normalize_key(semantic_key)
        song = self.store.active_song()
        if song is None:
            raise ValidationError("Start or select a Song before deferring a suggestion")
        session = self.sessions.latest_for_song(song.id)
        if session is None:
            raise ValidationError("Start a work Session before deferring a suggestion")
        existing = self._conn.execute(
            "SELECT seq,id,artist_id,song_id,session_id,semantic_key,scope FROM suggestion_deferrals "
            "WHERE artist_id=? AND song_id=? AND session_id=? AND semantic_key=? AND scope=?",
            (self.store.primary_artist_id, song.id, session.id, key, LATER_THIS_SONG),
        ).fetchone()
        if existing is not None:
            return self._record(existing)
        deferral_id = f"defer_{uuid.uuid4().hex}"
        try:
            with self.store._tx():
                self._conn.execute(
                    "INSERT INTO suggestion_deferrals(id,artist_id,song_id,session_id,semantic_key,scope) "
                    "VALUES(?,?,?,?,?,?)",
                    (deferral_id, self.store.primary_artist_id, song.id, session.id, key, LATER_THIS_SONG),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"cannot defer suggestion: {exc}") from exc
        row = self._conn.execute(
            "SELECT seq,id,artist_id,song_id,session_id,semantic_key,scope FROM suggestion_deferrals WHERE id=?",
            (deferral_id,),
        ).fetchone()
        if row is None:
            raise LineageCorruptionError("new suggestion deferral disappeared")
        return self._record(row)

    def is_deferred_now(self, semantic_key: str) -> bool:
        key = self._normalize_key(semantic_key)
        song = self.store.active_song()
        if song is None:
            return False
        session = self.sessions.latest_for_song(song.id)
        if session is None:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM suggestion_deferrals WHERE artist_id=? AND song_id=? AND session_id=? "
            "AND semantic_key=? AND scope=? LIMIT 1",
            (self.store.primary_artist_id, song.id, session.id, key, LATER_THIS_SONG),
        ).fetchone()
        return row is not None

    def history(self) -> tuple[SuggestionDeferral, ...]:
        return tuple(
            self._record(row)
            for row in self._conn.execute(
                "SELECT seq,id,artist_id,song_id,session_id,semantic_key,scope FROM suggestion_deferrals ORDER BY seq"
            )
        )
