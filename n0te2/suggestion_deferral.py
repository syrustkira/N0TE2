from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from typing import Iterable

from .lineage import LineageCorruptionError, LineageStore, ValidationError
from .session import SessionMemory

SUGGESTION_DEFERRAL_SCHEMA_VERSION = 2
LATER_THIS_SONG = "LATER_THIS_SONG"
AFTER_RELEASE = "AFTER_RELEASE"
NEXT_SONG = "NEXT_SONG"
SOMEDAY = "SOMEDAY"
NEVER_SUGGEST_AGAIN = "NEVER_SUGGEST_AGAIN"
SUGGESTION_DEFERRAL_HORIZONS = (
    LATER_THIS_SONG,
    AFTER_RELEASE,
    NEXT_SONG,
    SOMEDAY,
    NEVER_SUGGEST_AGAIN,
)


@dataclass(frozen=True)
class SuggestionDeferral:
    sequence: int
    id: str
    deferral_id: str
    action: str
    artist_id: str
    song_id: str
    session_id: str | None
    semantic_key: str
    horizon: str

    @property
    def scope(self) -> str:
        """Backward-compatible name used by the first bounded v1 contract."""
        return self.horizon


class SuggestionDeferralMemory:
    """Append-only artist-owned deferral history for stable suggestion semantics.

    Suggestion content remains owned by the suggestion catalog/service. This
    ledger stores only an artist instruction about when a stable semantic key
    may be surfaced again. Restoring a suggestion appends a RESTORE event; it
    never deletes or rewrites the original decision.
    """

    _TRIGGERS = {
        "suggestion_deferral_binding_valid",
        "suggestion_deferral_restore_valid",
        "suggestion_deferral_immutable",
        "suggestion_deferral_delete_immutable",
        "suggestion_deferral_activity",
        "suggestion_deferral_restore_activity",
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
        if not exists and version is None:
            if not self._table_exists("sessions") or not self._table_exists("activity_events"):
                raise LineageCorruptionError("Suggestion deferral requires canonical Session and Activity memory first")
            self._create_v2_schema()
            return
        if not exists or version is None:
            raise LineageCorruptionError("Suggestion deferral schema metadata/table mismatch")
        if version == "1":
            self._migrate_v1_to_v2()
            return
        if version != str(SUGGESTION_DEFERRAL_SCHEMA_VERSION):
            raise LineageCorruptionError("unsupported suggestion deferral schema version")

    def _create_v2_schema(self) -> None:
        try:
            with self.store._tx():
                self._create_v2_table_and_triggers()
                self._conn.execute(
                    "INSERT INTO metadata(key,value) VALUES('suggestion_deferral_schema_version',?)",
                    (str(SUGGESTION_DEFERRAL_SCHEMA_VERSION),),
                )
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError("cannot initialize suggestion deferral memory") from exc

    def _migrate_v1_to_v2(self) -> None:
        """Losslessly lift the shipped LATER_THIS_SONG ledger into v2 events."""
        try:
            with self.store._tx():
                for name in (
                    "suggestion_deferral_binding_valid",
                    "suggestion_deferral_immutable",
                    "suggestion_deferral_delete_immutable",
                    "suggestion_deferral_activity",
                ):
                    self._conn.execute(f"DROP TRIGGER IF EXISTS {name}")
                self._conn.execute("ALTER TABLE suggestion_deferrals RENAME TO suggestion_deferrals_v1")
                self._create_v2_table_and_triggers()
                rows = self._conn.execute(
                    "SELECT seq,id,artist_id,song_id,session_id,semantic_key,scope "
                    "FROM suggestion_deferrals_v1 ORDER BY seq"
                ).fetchall()
                for row in rows:
                    if str(row["scope"]) != LATER_THIS_SONG:
                        raise LineageCorruptionError("v1 suggestion deferral contains unsupported scope")
                    legacy_id = str(row["id"])
                    self._conn.execute(
                        "INSERT INTO suggestion_deferrals("
                        "id,deferral_id,action,artist_id,song_id,session_id,semantic_key,horizon"
                        ") VALUES(?,?,?,?,?,?,?,?)",
                        (
                            legacy_id,
                            legacy_id,
                            "DEFER",
                            str(row["artist_id"]),
                            str(row["song_id"]),
                            str(row["session_id"]),
                            str(row["semantic_key"]),
                            LATER_THIS_SONG,
                        ),
                    )
                self._conn.execute("DROP TABLE suggestion_deferrals_v1")
                self._conn.execute(
                    "UPDATE metadata SET value=? WHERE key='suggestion_deferral_schema_version'",
                    (str(SUGGESTION_DEFERRAL_SCHEMA_VERSION),),
                )
        except LineageCorruptionError:
            raise
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError("cannot migrate suggestion deferral memory") from exc

    def _create_v2_table_and_triggers(self) -> None:
        horizons = ",".join(f"'{item}'" for item in SUGGESTION_DEFERRAL_HORIZONS)
        self._conn.execute(
            f"""CREATE TABLE suggestion_deferrals (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                deferral_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('DEFER','RESTORE')),
                artist_id TEXT NOT NULL REFERENCES artists(id),
                song_id TEXT NOT NULL REFERENCES songs(id),
                session_id TEXT NULL REFERENCES sessions(id),
                semantic_key TEXT NOT NULL,
                horizon TEXT NOT NULL CHECK(horizon IN ({horizons})),
                UNIQUE(deferral_id,action)
            )"""
        )
        for statement in self._trigger_statements():
            self._conn.execute(statement)

    @staticmethod
    def _trigger_statements() -> tuple[str, ...]:
        return (
            """CREATE TRIGGER suggestion_deferral_binding_valid
            BEFORE INSERT ON suggestion_deferrals
            WHEN NEW.action='DEFER' AND (
                NOT EXISTS (
                    SELECT 1 FROM songs s
                    WHERE s.id=NEW.song_id AND s.artist_id=NEW.artist_id
                )
                OR (
                    NEW.horizon='LATER_THIS_SONG' AND (
                        NEW.session_id IS NULL OR NOT EXISTS (
                            SELECT 1 FROM sessions x
                            WHERE x.id=NEW.session_id AND x.song_id=NEW.song_id
                        )
                    )
                )
            )
            BEGIN
                SELECT RAISE(ABORT, 'Suggestion deferral binding is invalid');
            END""",
            """CREATE TRIGGER suggestion_deferral_restore_valid
            BEFORE INSERT ON suggestion_deferrals
            WHEN NEW.action='RESTORE' AND NOT EXISTS (
                SELECT 1 FROM suggestion_deferrals d
                WHERE d.deferral_id=NEW.deferral_id
                  AND d.action='DEFER'
                  AND d.artist_id=NEW.artist_id
                  AND d.song_id=NEW.song_id
                  AND d.semantic_key=NEW.semantic_key
                  AND d.horizon=NEW.horizon
            )
            BEGIN
                SELECT RAISE(ABORT, 'Suggestion deferral restore target is invalid');
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
            WHEN NEW.action='DEFER'
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,
                    object_type,object_id,payload_json
                ) VALUES(
                    'act_'||lower(hex(randomblob(16))),
                    'SUGGESTION_DEFERRED',NEW.artist_id,NEW.song_id,NULL,
                    'SUGGESTION_DEFERRAL',NEW.deferral_id,
                    '{\"horizon\":\"'||NEW.horizon||'\"}'
                );
            END""",
            """CREATE TRIGGER suggestion_deferral_restore_activity
            AFTER INSERT ON suggestion_deferrals
            WHEN NEW.action='RESTORE'
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,
                    object_type,object_id,payload_json
                ) VALUES(
                    'act_'||lower(hex(randomblob(16))),
                    'SUGGESTION_DEFERRAL_CLEARED',NEW.artist_id,NEW.song_id,NULL,
                    'SUGGESTION_DEFERRAL',NEW.deferral_id,
                    '{\"horizon\":\"'||NEW.horizon||'\"}'
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
    def normalize_horizon(value: str) -> str:
        horizon = str(value).strip().upper().replace("-", "_").replace(" ", "_")
        if horizon not in SUGGESTION_DEFERRAL_HORIZONS:
            raise ValidationError(f"unsupported suggestion deferral horizon: {horizon}")
        return horizon

    @staticmethod
    def _record(row: sqlite3.Row) -> SuggestionDeferral:
        return SuggestionDeferral(
            sequence=int(row["seq"]),
            id=str(row["id"]),
            deferral_id=str(row["deferral_id"]),
            action=str(row["action"]),
            artist_id=str(row["artist_id"]),
            song_id=str(row["song_id"]),
            session_id=None if row["session_id"] is None else str(row["session_id"]),
            semantic_key=str(row["semantic_key"]),
            horizon=str(row["horizon"]),
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
        for record in self.history():
            if record.artist_id != self.store.primary_artist_id:
                raise LineageCorruptionError("Suggestion deferral contains invalid profile scope")
            song = self.store.get_song(record.song_id)
            if song is None or song.artist_id != record.artist_id:
                raise LineageCorruptionError("Suggestion deferral references invalid Song")
            if record.horizon == LATER_THIS_SONG and record.session_id is None:
                raise LineageCorruptionError("Later-this-Song deferral is missing Session identity")

    def _active_rows(self, semantic_key: str | None = None) -> tuple[SuggestionDeferral, ...]:
        params: list[str] = [self.store.primary_artist_id]
        key_clause = ""
        if semantic_key is not None:
            key_clause = " AND d.semantic_key=?"
            params.append(self._normalize_key(semantic_key))
        rows = self._conn.execute(
            "SELECT d.seq,d.id,d.deferral_id,d.action,d.artist_id,d.song_id,d.session_id,d.semantic_key,d.horizon "
            "FROM suggestion_deferrals d "
            "WHERE d.artist_id=? AND d.action='DEFER'" + key_clause +
            " AND NOT EXISTS (SELECT 1 FROM suggestion_deferrals r "
            "WHERE r.deferral_id=d.deferral_id AND r.action='RESTORE') ORDER BY d.seq",
            tuple(params),
        ).fetchall()
        return tuple(self._record(row) for row in rows)

    def active_deferrals(self) -> tuple[SuggestionDeferral, ...]:
        return self._active_rows()

    def defer(self, semantic_key: str, horizon: str) -> SuggestionDeferral:
        key = self._normalize_key(semantic_key)
        normalized = self.normalize_horizon(horizon)
        song = self.store.active_song()
        if song is None:
            raise ValidationError("Start or select a Song before deferring a suggestion")
        session = self.sessions.latest_for_song(song.id)
        if normalized == LATER_THIS_SONG and session is None:
            raise ValidationError("Start a work Session before choosing Later this Song")
        session_id = None if session is None else session.id

        for existing in self._active_rows(key):
            same_context = (
                existing.horizon == normalized
                and (
                    normalized in {SOMEDAY, NEVER_SUGGEST_AGAIN}
                    or existing.song_id == song.id
                )
                and (
                    normalized != LATER_THIS_SONG
                    or existing.session_id == session_id
                )
            )
            if same_context:
                return existing

        deferral_id = f"defer_{uuid.uuid4().hex}"
        event_id = f"defer_event_{uuid.uuid4().hex}"
        try:
            with self.store._tx():
                self._conn.execute(
                    "INSERT INTO suggestion_deferrals("
                    "id,deferral_id,action,artist_id,song_id,session_id,semantic_key,horizon"
                    ") VALUES(?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        deferral_id,
                        "DEFER",
                        self.store.primary_artist_id,
                        song.id,
                        session_id,
                        key,
                        normalized,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"cannot defer suggestion: {exc}") from exc
        return self._by_event_id(event_id)

    def defer_later_this_song(self, semantic_key: str) -> SuggestionDeferral:
        return self.defer(semantic_key, LATER_THIS_SONG)

    def _by_event_id(self, event_id: str) -> SuggestionDeferral:
        row = self._conn.execute(
            "SELECT seq,id,deferral_id,action,artist_id,song_id,session_id,semantic_key,horizon "
            "FROM suggestion_deferrals WHERE id=?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise LineageCorruptionError("suggestion deferral event disappeared")
        return self._record(row)

    def restore(self, deferral_id: str) -> SuggestionDeferral:
        target = str(deferral_id).strip()
        if not target:
            raise ValidationError("suggestion deferral identity is required")
        row = self._conn.execute(
            "SELECT d.seq,d.id,d.deferral_id,d.action,d.artist_id,d.song_id,d.session_id,d.semantic_key,d.horizon "
            "FROM suggestion_deferrals d WHERE d.deferral_id=? AND d.artist_id=? AND d.action='DEFER'",
            (target, self.store.primary_artist_id),
        ).fetchone()
        if row is None:
            raise ValidationError("suggestion deferral does not exist for this Artist")
        deferred = self._record(row)
        existing = self._conn.execute(
            "SELECT seq,id,deferral_id,action,artist_id,song_id,session_id,semantic_key,horizon "
            "FROM suggestion_deferrals WHERE deferral_id=? AND action='RESTORE'",
            (target,),
        ).fetchone()
        if existing is not None:
            return self._record(existing)
        event_id = f"restore_event_{uuid.uuid4().hex}"
        try:
            with self.store._tx():
                self._conn.execute(
                    "INSERT INTO suggestion_deferrals("
                    "id,deferral_id,action,artist_id,song_id,session_id,semantic_key,horizon"
                    ") VALUES(?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        deferred.deferral_id,
                        "RESTORE",
                        deferred.artist_id,
                        deferred.song_id,
                        deferred.session_id,
                        deferred.semantic_key,
                        deferred.horizon,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"cannot restore suggestion: {exc}") from exc
        return self._by_event_id(event_id)

    def applies(
        self,
        semantic_key: str,
        *,
        released_song_ids: Iterable[str] = (),
    ) -> bool:
        key = self._normalize_key(semantic_key)
        song = self.store.active_song()
        if song is None:
            return False
        session = self.sessions.latest_for_song(song.id)
        released = {str(item) for item in released_song_ids}
        for item in self._active_rows(key):
            if item.horizon == LATER_THIS_SONG:
                if song.id == item.song_id and session is not None and session.id == item.session_id:
                    return True
            elif item.horizon == AFTER_RELEASE:
                if song.id == item.song_id and item.song_id not in released:
                    return True
            elif item.horizon == NEXT_SONG:
                if song.id == item.song_id:
                    return True
            elif item.horizon in {SOMEDAY, NEVER_SUGGEST_AGAIN}:
                return True
        return False

    def is_deferred_now(self, semantic_key: str) -> bool:
        return self.applies(semantic_key)

    def history(self) -> tuple[SuggestionDeferral, ...]:
        return tuple(
            self._record(row)
            for row in self._conn.execute(
                "SELECT seq,id,deferral_id,action,artist_id,song_id,session_id,semantic_key,horizon "
                "FROM suggestion_deferrals ORDER BY seq"
            )
        )
