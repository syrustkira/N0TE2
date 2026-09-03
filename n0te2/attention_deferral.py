from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Iterable

from .attention import AttentionMemory
from .lineage import LineageCorruptionError, NotFoundError, ValidationError

DEFERRAL_SCHEMA_VERSION = 1
DEFERRAL_HORIZONS = (
    "LATER_THIS_SONG",
    "AFTER_RELEASE",
    "NEXT_SONG",
    "SOMEDAY",
    "NEVER_SUGGEST_AGAIN",
)
DEFERRAL_STATES = {"ACTIVE", "CLEARED"}
DEFERRAL_CLEAR_REASONS = {"RESTORED", "SUPERSEDED"}
_ITEM_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,31}:[a-z0-9][a-z0-9:._-]{0,159}$")


@dataclass(frozen=True)
class AttentionDeferral:
    sequence: int
    id: str
    artist_id: str
    item_key: str
    song_id: str | None
    horizon: str
    anchor: str | None
    state: str
    clear_reason: str | None


class AttentionDeferralMemory:
    """Durable, reversible Not Now state owned by canonical Attention memory.

    A deferral never owns the suggestion/job itself. It records only the artist's
    suppression instruction for a stable semantic item key. Consumers must ask
    ``applies`` against current Artist/Song/context evidence before surfacing that
    item again.
    """

    _TRIGGER_NAMES = {
        "attention_deferral_song_same_artist",
        "attention_deferral_binding_immutable",
        "attention_deferral_cleared_immutable",
        "attention_deferral_delete_immutable",
        "attention_deferral_clear_shape",
        "attention_deferral_started_activity",
        "attention_deferral_cleared_activity",
    }
    _INDEX_NAMES = {"attention_one_active_deferral_per_item"}

    def __init__(self, attention: AttentionMemory):
        if not isinstance(attention, AttentionMemory):
            raise TypeError("AttentionDeferralMemory requires canonical AttentionMemory")
        self.attention = attention
        self.store = attention.store
        self._conn = self.store._conn
        self._ensure_schema()
        self._validate_existing()

    def _table_exists(self, name: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def _metadata_value(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        return None if row is None else str(row["value"])

    @staticmethod
    def _trigger_statements() -> tuple[str, ...]:
        return (
            """CREATE TRIGGER attention_deferral_song_same_artist
            BEFORE INSERT ON attention_deferrals
            WHEN NEW.song_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM songs s
                WHERE s.id=NEW.song_id AND s.artist_id=NEW.artist_id
            ) BEGIN
                SELECT RAISE(ABORT, 'Deferral Song belongs to a different Artist');
            END""",
            """CREATE TRIGGER attention_deferral_binding_immutable
            BEFORE UPDATE ON attention_deferrals
            WHEN NEW.id<>OLD.id OR NEW.artist_id<>OLD.artist_id
              OR NEW.item_key<>OLD.item_key OR NEW.song_id IS NOT OLD.song_id
              OR NEW.horizon<>OLD.horizon OR NEW.anchor IS NOT OLD.anchor
            BEGIN
                SELECT RAISE(ABORT, 'Deferral identity and binding are immutable');
            END""",
            """CREATE TRIGGER attention_deferral_cleared_immutable
            BEFORE UPDATE ON attention_deferrals
            WHEN OLD.state='CLEARED'
            BEGIN SELECT RAISE(ABORT, 'cleared Deferral is immutable'); END""",
            """CREATE TRIGGER attention_deferral_delete_immutable
            BEFORE DELETE ON attention_deferrals
            BEGIN SELECT RAISE(ABORT, 'Deferral history is immutable'); END""",
            """CREATE TRIGGER attention_deferral_clear_shape
            BEFORE UPDATE ON attention_deferrals
            WHEN NOT (
                OLD.state='ACTIVE' AND NEW.state='CLEARED'
                AND NEW.clear_reason IN ('RESTORED','SUPERSEDED')
            ) BEGIN
                SELECT RAISE(ABORT, 'Deferral may only transition ACTIVE to CLEARED');
            END""",
            """CREATE TRIGGER attention_deferral_started_activity
            AFTER INSERT ON attention_deferrals
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,
                    object_type,object_id,payload_json
                ) VALUES(
                    'act_'||lower(hex(randomblob(16))),
                    'ATTENTION_DEFERRED',NEW.artist_id,NEW.song_id,NULL,
                    'ATTENTION_DEFERRAL',NEW.id,
                    '{\"item_key\":\"'||NEW.item_key||'\",\"horizon\":\"'||NEW.horizon||'\"}'
                );
            END""",
            """CREATE TRIGGER attention_deferral_cleared_activity
            AFTER UPDATE OF state ON attention_deferrals
            WHEN OLD.state='ACTIVE' AND NEW.state='CLEARED'
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,
                    object_type,object_id,payload_json
                ) VALUES(
                    'act_'||lower(hex(randomblob(16))),
                    'ATTENTION_DEFERRAL_CLEARED',NEW.artist_id,NEW.song_id,NULL,
                    'ATTENTION_DEFERRAL',NEW.id,
                    '{\"item_key\":\"'||NEW.item_key||'\",\"reason\":\"'||NEW.clear_reason||'\"}'
                );
            END""",
        )

    def _ensure_schema(self) -> None:
        exists = self._table_exists("attention_deferrals")
        version = self._metadata_value("attention_deferral_schema_version")
        if exists or version is not None:
            if not exists or version != str(DEFERRAL_SCHEMA_VERSION):
                raise LineageCorruptionError("Attention deferral schema metadata/table mismatch")
            return
        if not self._table_exists("attention_focus_sessions") or not self._table_exists("activity_events"):
            raise LineageCorruptionError(
                "Attention deferrals require canonical Attention and Activity first"
            )
        try:
            with self.store._tx():
                self._conn.execute(
                    """CREATE TABLE attention_deferrals (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        artist_id TEXT NOT NULL REFERENCES artists(id),
                        item_key TEXT NOT NULL CHECK(length(trim(item_key)) > 0),
                        song_id TEXT NULL REFERENCES songs(id),
                        horizon TEXT NOT NULL CHECK(horizon IN (
                            'LATER_THIS_SONG','AFTER_RELEASE','NEXT_SONG',
                            'SOMEDAY','NEVER_SUGGEST_AGAIN'
                        )),
                        anchor TEXT NULL,
                        state TEXT NOT NULL DEFAULT 'ACTIVE'
                            CHECK(state IN ('ACTIVE','CLEARED')),
                        clear_reason TEXT NULL CHECK(
                            clear_reason IS NULL OR clear_reason IN ('RESTORED','SUPERSEDED')
                        )
                    )"""
                )
                self._conn.execute(
                    "CREATE UNIQUE INDEX attention_one_active_deferral_per_item "
                    "ON attention_deferrals(artist_id,item_key) WHERE state='ACTIVE'"
                )
                for statement in self._trigger_statements():
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT INTO metadata(key,value) VALUES('attention_deferral_schema_version',?)",
                    (str(DEFERRAL_SCHEMA_VERSION),),
                )
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError("cannot initialize Attention deferrals") from exc

    @staticmethod
    def _clean_item_key(value: str) -> str:
        item = str(value).strip()
        if not _ITEM_KEY.fullmatch(item):
            raise ValidationError("item_key must be a bounded stable semantic key")
        return item

    @staticmethod
    def _normalize_horizon(value: str) -> str:
        horizon = str(value).strip().upper().replace("-", "_").replace(" ", "_")
        if horizon not in DEFERRAL_HORIZONS:
            raise ValidationError(f"unsupported Not Now horizon: {horizon}")
        return horizon

    @staticmethod
    def _row(row: sqlite3.Row) -> AttentionDeferral:
        return AttentionDeferral(
            sequence=int(row["seq"]),
            id=str(row["id"]),
            artist_id=str(row["artist_id"]),
            item_key=str(row["item_key"]),
            song_id=None if row["song_id"] is None else str(row["song_id"]),
            horizon=str(row["horizon"]),
            anchor=None if row["anchor"] is None else str(row["anchor"]),
            state=str(row["state"]),
            clear_reason=None if row["clear_reason"] is None else str(row["clear_reason"]),
        )

    def _validate_existing(self) -> None:
        try:
            if self._metadata_value("attention_deferral_schema_version") != str(
                DEFERRAL_SCHEMA_VERSION
            ):
                raise LineageCorruptionError("unsupported Attention deferral schema version")
            trigger_names = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'attention_deferral_%'"
                )
            }
            missing = self._TRIGGER_NAMES - trigger_names
            if missing:
                raise LineageCorruptionError(
                    f"Attention deferral integrity hooks are incomplete: {sorted(missing)}"
                )
            index_names = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name LIKE 'attention_%deferral%'"
                )
            }
            if not self._INDEX_NAMES.issubset(index_names):
                raise LineageCorruptionError("Attention deferral uniqueness hook is missing")
            for row in self._conn.execute(
                "SELECT seq,id,artist_id,item_key,song_id,horizon,anchor,state,clear_reason "
                "FROM attention_deferrals ORDER BY seq"
            ):
                item = self._row(row)
                self._clean_item_key(item.item_key)
                if item.artist_id != self.store.primary_artist_id:
                    raise LineageCorruptionError("Deferral artist does not match active profile")
                if item.horizon not in DEFERRAL_HORIZONS or item.state not in DEFERRAL_STATES:
                    raise LineageCorruptionError("Deferral contains invalid state")
                if item.song_id is not None:
                    song = self.store.get_song(item.song_id)
                    if song is None or song.artist_id != item.artist_id:
                        raise LineageCorruptionError("Deferral is bound to invalid Song/Artist")
                if item.state == "ACTIVE" and item.clear_reason is not None:
                    raise LineageCorruptionError("active Deferral contains clear reason")
                if item.state == "CLEARED" and item.clear_reason not in DEFERRAL_CLEAR_REASONS:
                    raise LineageCorruptionError("cleared Deferral is missing clear reason")
                if item.horizon == "LATER_THIS_SONG" and (
                    item.song_id is None or not item.anchor
                ):
                    raise LineageCorruptionError(
                        "Later-this-Song Deferral requires Song and context anchor"
                    )
                if item.horizon in {"NEXT_SONG", "AFTER_RELEASE"} and item.song_id is None:
                    raise LineageCorruptionError(
                        f"{item.horizon} Deferral requires Song"
                    )
                if item.horizon in {"SOMEDAY", "NEVER_SUGGEST_AGAIN"} and (
                    item.song_id is not None or item.anchor is not None
                ):
                    raise LineageCorruptionError(
                        f"{item.horizon} Deferral must be Artist-scoped"
                    )
        except LineageCorruptionError:
            raise
        except (sqlite3.DatabaseError, ValueError, TypeError, ValidationError) as exc:
            raise LineageCorruptionError("Attention deferral state is unreadable or corrupt") from exc

    def active(self, item_key: str) -> AttentionDeferral | None:
        key = self._clean_item_key(item_key)
        row = self._conn.execute(
            "SELECT seq,id,artist_id,item_key,song_id,horizon,anchor,state,clear_reason "
            "FROM attention_deferrals WHERE artist_id=? AND item_key=? AND state='ACTIVE' "
            "ORDER BY seq DESC LIMIT 1",
            (self.store.primary_artist_id, key),
        ).fetchone()
        return None if row is None else self._row(row)

    def active_items(self, *, prefix: str | None = None) -> tuple[AttentionDeferral, ...]:
        rows = self._conn.execute(
            "SELECT seq,id,artist_id,item_key,song_id,horizon,anchor,state,clear_reason "
            "FROM attention_deferrals WHERE artist_id=? AND state='ACTIVE' ORDER BY seq",
            (self.store.primary_artist_id,),
        ).fetchall()
        items = tuple(self._row(row) for row in rows)
        if prefix is None:
            return items
        bounded = str(prefix).strip()
        return tuple(item for item in items if item.item_key.startswith(bounded))

    def history(self, item_key: str | None = None) -> tuple[AttentionDeferral, ...]:
        if item_key is None:
            rows = self._conn.execute(
                "SELECT seq,id,artist_id,item_key,song_id,horizon,anchor,state,clear_reason "
                "FROM attention_deferrals WHERE artist_id=? ORDER BY seq",
                (self.store.primary_artist_id,),
            ).fetchall()
        else:
            key = self._clean_item_key(item_key)
            rows = self._conn.execute(
                "SELECT seq,id,artist_id,item_key,song_id,horizon,anchor,state,clear_reason "
                "FROM attention_deferrals WHERE artist_id=? AND item_key=? ORDER BY seq",
                (self.store.primary_artist_id, key),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def _validate_binding(
        self,
        horizon: str,
        song_id: str | None,
        anchor: str | None,
    ) -> tuple[str | None, str | None]:
        song = None if song_id is None else str(song_id).strip()
        anchored = None if anchor is None else str(anchor).strip()
        if song:
            found = self.store.get_song(song)
            if found is None:
                raise NotFoundError(
                    f"Song not found in profile {self.store.profile_id}: {song}"
                )
            if found.artist_id != self.store.primary_artist_id:
                raise ValidationError("Deferral Song belongs to a different Artist")
        if horizon == "LATER_THIS_SONG":
            if not song or not anchored:
                raise ValidationError("Later this Song requires current Song and context anchor")
        elif horizon in {"NEXT_SONG", "AFTER_RELEASE"}:
            if not song:
                raise ValidationError(f"{horizon} requires current Song")
            anchored = None
        else:
            song = None
            anchored = None
        return song, anchored

    def defer(
        self,
        item_key: str,
        horizon: str,
        *,
        song_id: str | None = None,
        anchor: str | None = None,
    ) -> AttentionDeferral:
        key = self._clean_item_key(item_key)
        normalized = self._normalize_horizon(horizon)
        song_id, anchor = self._validate_binding(normalized, song_id, anchor)
        current = self.active(key)
        if current is not None and (
            current.horizon == normalized
            and current.song_id == song_id
            and current.anchor == anchor
        ):
            return current
        new_id = f"defer_{uuid.uuid4().hex}"
        try:
            with self.store._tx():
                if current is not None:
                    changed = self._conn.execute(
                        "UPDATE attention_deferrals "
                        "SET state='CLEARED',clear_reason='SUPERSEDED' "
                        "WHERE id=? AND state='ACTIVE'",
                        (current.id,),
                    ).rowcount
                    if changed != 1:
                        raise LineageCorruptionError("active Deferral changed during supersession")
                self._conn.execute(
                    "INSERT INTO attention_deferrals("
                    "id,artist_id,item_key,song_id,horizon,anchor,state,clear_reason"
                    ") VALUES(?,?,?,?,?,?,'ACTIVE',NULL)",
                    (
                        new_id,
                        self.store.primary_artist_id,
                        key,
                        song_id,
                        normalized,
                        anchor,
                    ),
                )
        except LineageCorruptionError:
            raise
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"cannot defer N0TE item: {exc}") from exc
        active = self.active(key)
        if active is None or active.id != new_id:
            raise LineageCorruptionError("new Deferral did not become active")
        return active

    def restore(self, item_key: str) -> AttentionDeferral | None:
        current = self.active(item_key)
        if current is None:
            return None
        try:
            with self.store._tx():
                changed = self._conn.execute(
                    "UPDATE attention_deferrals "
                    "SET state='CLEARED',clear_reason='RESTORED' "
                    "WHERE id=? AND state='ACTIVE'",
                    (current.id,),
                ).rowcount
                if changed != 1:
                    raise LineageCorruptionError("active Deferral changed during restore")
        except LineageCorruptionError:
            raise
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"cannot restore deferred N0TE item: {exc}") from exc
        return self.history(current.item_key)[-1]

    def applies(
        self,
        item_key: str,
        *,
        song_id: str | None = None,
        anchor: str | None = None,
        released_song_ids: Iterable[str] = (),
    ) -> bool:
        current = self.active(item_key)
        if current is None:
            return False
        current_song = None if song_id is None else str(song_id).strip()
        current_anchor = None if anchor is None else str(anchor).strip()
        if current.horizon == "LATER_THIS_SONG":
            return current_song == current.song_id and current_anchor == current.anchor
        if current.horizon == "NEXT_SONG":
            return current_song == current.song_id
        if current.horizon == "AFTER_RELEASE":
            released = {str(value).strip() for value in released_song_ids}
            return current_song == current.song_id and current.song_id not in released
        return True
