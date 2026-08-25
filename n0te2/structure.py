from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from .lineage import LineageCorruptionError, LineageStore, NotFoundError, ValidationError

STRUCTURE_SCHEMA_VERSION = 1
STRUCTURE_CHANGE_KINDS = {"ADD_SECTION", "EDIT_SECTION", "REMOVE_SECTION", "RESTORE_PREVIOUS"}


class StaleSongStructureError(RuntimeError):
    """The Song or current Structure revision moved after authority was prepared."""


@dataclass(frozen=True)
class SongSection:
    label: str
    first_bar: int
    last_bar: int
    note: str | None = None


@dataclass(frozen=True)
class SongStructureRevision:
    sequence: int
    id: str
    song_id: str
    parent_revision_id: str | None
    change_kind: str
    sections: tuple[SongSection, ...]


class SongStructureMemory:
    """Revisioned host-neutral Song section map.

    Structure revisions are immutable snapshots. One pointer per Song identifies the
    current snapshot. Consumer edits create another revision instead of rewriting
    history; restoring prior state also creates a new revision so chronology remains
    linear and auditable.
    """

    _TRIGGER_NAMES = {
        "structure_revisions_immutable_update",
        "structure_revisions_immutable_delete",
        "structure_parent_same_song",
        "structure_state_revision_same_song_insert",
        "structure_state_revision_same_song_update",
        "structure_state_no_delete",
        "structure_revision_activity",
    }

    def __init__(self, store: LineageStore):
        if not isinstance(store, LineageStore):
            raise TypeError("SongStructureMemory requires the canonical LineageStore")
        self.store = store
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
        revisions = self._table_exists("song_structure_revisions")
        state = self._table_exists("song_structure_state")
        version = self._metadata_value("structure_schema_version")
        if revisions or state or version is not None:
            if not revisions or not state or version != str(STRUCTURE_SCHEMA_VERSION):
                raise LineageCorruptionError("Structure schema metadata/table mismatch")
            return
        if not self._table_exists("activity_events"):
            raise LineageCorruptionError("SongStructureMemory requires canonical Activity first")
        try:
            with self.store._tx():
                self._conn.execute(
                    """CREATE TABLE song_structure_revisions (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        song_id TEXT NOT NULL REFERENCES songs(id),
                        parent_revision_id TEXT NULL REFERENCES song_structure_revisions(id),
                        change_kind TEXT NOT NULL CHECK(change_kind IN (
                            'ADD_SECTION','EDIT_SECTION','REMOVE_SECTION','RESTORE_PREVIOUS'
                        )),
                        sections_json TEXT NOT NULL
                    )"""
                )
                self._conn.execute(
                    "CREATE INDEX structure_song_history ON song_structure_revisions(song_id,seq)"
                )
                self._conn.execute(
                    """CREATE TABLE song_structure_state (
                        song_id TEXT PRIMARY KEY REFERENCES songs(id),
                        current_revision_id TEXT NOT NULL UNIQUE REFERENCES song_structure_revisions(id)
                    )"""
                )
                for statement in self._trigger_statements():
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT INTO metadata(key,value) VALUES('structure_schema_version',?)",
                    (str(STRUCTURE_SCHEMA_VERSION),),
                )
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError("cannot initialize Song Structure memory") from exc

    @staticmethod
    def _trigger_statements() -> tuple[str, ...]:
        return (
            """CREATE TRIGGER structure_revisions_immutable_update
            BEFORE UPDATE ON song_structure_revisions BEGIN
                SELECT RAISE(ABORT, 'Song Structure revisions are immutable');
            END""",
            """CREATE TRIGGER structure_revisions_immutable_delete
            BEFORE DELETE ON song_structure_revisions BEGIN
                SELECT RAISE(ABORT, 'Song Structure revisions are immutable');
            END""",
            """CREATE TRIGGER structure_parent_same_song
            BEFORE INSERT ON song_structure_revisions
            WHEN NEW.parent_revision_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM song_structure_revisions p
                WHERE p.id=NEW.parent_revision_id AND p.song_id=NEW.song_id
            ) BEGIN
                SELECT RAISE(ABORT, 'Structure parent revision belongs to a different Song');
            END""",
            """CREATE TRIGGER structure_state_revision_same_song_insert
            BEFORE INSERT ON song_structure_state
            WHEN NOT EXISTS (
                SELECT 1 FROM song_structure_revisions r
                WHERE r.id=NEW.current_revision_id AND r.song_id=NEW.song_id
            ) BEGIN
                SELECT RAISE(ABORT, 'Structure current revision belongs to a different Song');
            END""",
            """CREATE TRIGGER structure_state_revision_same_song_update
            BEFORE UPDATE OF current_revision_id ON song_structure_state
            WHEN NOT EXISTS (
                SELECT 1 FROM song_structure_revisions r
                WHERE r.id=NEW.current_revision_id AND r.song_id=NEW.song_id
            ) BEGIN
                SELECT RAISE(ABORT, 'Structure current revision belongs to a different Song');
            END""",
            """CREATE TRIGGER structure_state_no_delete
            BEFORE DELETE ON song_structure_state BEGIN
                SELECT RAISE(ABORT, 'Song Structure current state cannot be deleted');
            END""",
            """CREATE TRIGGER structure_revision_activity
            AFTER INSERT ON song_structure_revisions
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,object_type,object_id,payload_json
                )
                SELECT
                    'act_'||lower(hex(randomblob(16))),
                    'SONG_STRUCTURE_REVISED',
                    s.artist_id,
                    NEW.song_id,
                    s.current_version_id,
                    'SONG_STRUCTURE_REVISION',
                    NEW.id,
                    '{"change_kind":"'||NEW.change_kind||'"}'
                FROM songs s WHERE s.id=NEW.song_id;
            END""",
        )

    @staticmethod
    def _clean_text(value: str, field: str, maximum: int) -> str:
        text = " ".join(str(value).split())
        if not text:
            raise ValidationError(f"{field} must not be empty")
        if len(text) > maximum:
            raise ValidationError(f"{field} is too long")
        return text

    @staticmethod
    def _optional_note(value: str | None) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split())
        if not text:
            return None
        if len(text) > 600:
            raise ValidationError("section note is too long")
        return text

    @staticmethod
    def _bar(value: int, field: str) -> int:
        if isinstance(value, bool):
            raise ValidationError(f"{field} must be a positive whole bar")
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{field} must be a positive whole bar") from exc
        if number < 1 or number > 999999:
            raise ValidationError(f"{field} must be between 1 and 999999")
        return number

    @classmethod
    def section(cls, label: str, first_bar: int, last_bar: int, note: str | None = None) -> SongSection:
        label = cls._clean_text(label, "section name", 120)
        first = cls._bar(first_bar, "first bar")
        last = cls._bar(last_bar, "last bar")
        if last < first:
            raise ValidationError("last bar must be on or after first bar")
        return SongSection(label, first, last, cls._optional_note(note))

    @classmethod
    def _normalize_sections(cls, sections: tuple[SongSection, ...]) -> tuple[SongSection, ...]:
        normalized = tuple(cls.section(item.label, item.first_bar, item.last_bar, item.note) for item in sections)
        ordered = tuple(sorted(normalized, key=lambda item: (item.first_bar, item.last_bar, item.label.casefold())))
        previous: SongSection | None = None
        for item in ordered:
            if previous is not None and item.first_bar <= previous.last_bar:
                raise ValidationError(
                    f"Song sections overlap: {previous.label} and {item.label}"
                )
            previous = item
        return ordered

    @classmethod
    def _sections_json(cls, sections: tuple[SongSection, ...]) -> str:
        normalized = cls._normalize_sections(sections)
        return json.dumps(
            [
                {
                    "label": item.label,
                    "first_bar": item.first_bar,
                    "last_bar": item.last_bar,
                    "note": item.note,
                }
                for item in normalized
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def _decode_sections(cls, raw: str) -> tuple[SongSection, ...]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LineageCorruptionError("Song Structure sections JSON is invalid") from exc
        if not isinstance(value, list):
            raise LineageCorruptionError("Song Structure sections must be a list")
        sections: list[SongSection] = []
        try:
            for row in value:
                if not isinstance(row, dict) or set(row) != {"label", "first_bar", "last_bar", "note"}:
                    raise LineageCorruptionError("Song Structure section shape is invalid")
                sections.append(
                    cls.section(row["label"], row["first_bar"], row["last_bar"], row["note"])
                )
            normalized = cls._normalize_sections(tuple(sections))
        except ValidationError as exc:
            raise LineageCorruptionError(f"Song Structure section is invalid: {exc}") from exc
        if tuple(sections) != normalized:
            raise LineageCorruptionError("Song Structure sections are not in canonical bar order")
        return normalized

    def _revision(self, row: sqlite3.Row) -> SongStructureRevision:
        return SongStructureRevision(
            sequence=int(row["seq"]),
            id=str(row["id"]),
            song_id=str(row["song_id"]),
            parent_revision_id=None if row["parent_revision_id"] is None else str(row["parent_revision_id"]),
            change_kind=str(row["change_kind"]),
            sections=self._decode_sections(str(row["sections_json"])),
        )

    def get_revision(self, revision_id: str) -> SongStructureRevision | None:
        row = self._conn.execute(
            "SELECT seq,id,song_id,parent_revision_id,change_kind,sections_json "
            "FROM song_structure_revisions WHERE id=?",
            (str(revision_id),),
        ).fetchone()
        return None if row is None else self._revision(row)

    def current(self, song_id: str) -> SongStructureRevision | None:
        if self.store.get_song(song_id) is None:
            raise NotFoundError(f"Song not found in profile {self.store.profile_id}: {song_id}")
        row = self._conn.execute(
            "SELECT r.seq,r.id,r.song_id,r.parent_revision_id,r.change_kind,r.sections_json "
            "FROM song_structure_state s JOIN song_structure_revisions r "
            "ON r.id=s.current_revision_id WHERE s.song_id=?",
            (song_id,),
        ).fetchone()
        return None if row is None else self._revision(row)

    def history(self, song_id: str) -> tuple[SongStructureRevision, ...]:
        if self.store.get_song(song_id) is None:
            raise NotFoundError(f"Song not found in profile {self.store.profile_id}: {song_id}")
        return tuple(
            self._revision(row)
            for row in self._conn.execute(
                "SELECT seq,id,song_id,parent_revision_id,change_kind,sections_json "
                "FROM song_structure_revisions WHERE song_id=? ORDER BY seq",
                (song_id,),
            )
        )

    def commit(
        self,
        *,
        song_id: str,
        sections: tuple[SongSection, ...],
        expected_revision_id: str | None,
        change_kind: str,
        require_active_song: bool = False,
    ) -> SongStructureRevision:
        song = self.store.get_song(song_id)
        if song is None:
            raise NotFoundError(f"Song not found in profile {self.store.profile_id}: {song_id}")
        change_kind = str(change_kind).strip().upper()
        if change_kind not in STRUCTURE_CHANGE_KINDS:
            raise ValidationError(f"unsupported Structure change kind: {change_kind}")
        sections_json = self._sections_json(sections)
        revision_id = f"structure_{uuid.uuid4().hex}"
        try:
            with self.store._tx():
                if require_active_song:
                    active = self._conn.execute(
                        "SELECT value FROM metadata WHERE key='active_song_id'"
                    ).fetchone()
                    if active is None or str(active["value"]) != song_id:
                        raise StaleSongStructureError(
                            "The active Song changed after this Structure action was prepared."
                        )
                state = self._conn.execute(
                    "SELECT current_revision_id FROM song_structure_state WHERE song_id=?",
                    (song_id,),
                ).fetchone()
                current_id = None if state is None else str(state["current_revision_id"])
                if current_id != expected_revision_id:
                    raise StaleSongStructureError(
                        "The Song Structure changed after this page was prepared."
                    )
                self._conn.execute(
                    "INSERT INTO song_structure_revisions("
                    "id,song_id,parent_revision_id,change_kind,sections_json) VALUES(?,?,?,?,?)",
                    (revision_id, song_id, current_id, change_kind, sections_json),
                )
                if state is None:
                    self._conn.execute(
                        "INSERT INTO song_structure_state(song_id,current_revision_id) VALUES(?,?)",
                        (song_id, revision_id),
                    )
                else:
                    updated = self._conn.execute(
                        "UPDATE song_structure_state SET current_revision_id=? "
                        "WHERE song_id=? AND current_revision_id=?",
                        (revision_id, song_id, current_id),
                    )
                    if updated.rowcount != 1:
                        raise StaleSongStructureError(
                            "The Song Structure changed before this revision committed."
                        )
        except StaleSongStructureError:
            raise
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"cannot commit Song Structure revision: {exc}") from exc
        result = self.get_revision(revision_id)
        if result is None:
            raise LineageCorruptionError("Song Structure revision disappeared after commit")
        return result

    def _validate_existing(self) -> None:
        try:
            if self._metadata_value("structure_schema_version") != str(STRUCTURE_SCHEMA_VERSION):
                raise LineageCorruptionError("unsupported Song Structure schema version")
            trigger_names = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'structure_%'"
                )
            }
            missing = self._TRIGGER_NAMES - trigger_names
            if missing:
                raise LineageCorruptionError(
                    f"Song Structure integrity hooks are incomplete: {sorted(missing)}"
                )
            for row in self._conn.execute(
                "SELECT seq,id,song_id,parent_revision_id,change_kind,sections_json "
                "FROM song_structure_revisions ORDER BY seq"
            ):
                revision = self._revision(row)
                if self.store.get_song(revision.song_id) is None:
                    raise LineageCorruptionError("Song Structure revision lost its Song")
                if revision.change_kind not in STRUCTURE_CHANGE_KINDS:
                    raise LineageCorruptionError("Song Structure revision has invalid change kind")
                if revision.parent_revision_id is not None:
                    parent = self.get_revision(revision.parent_revision_id)
                    if parent is None or parent.song_id != revision.song_id:
                        raise LineageCorruptionError("Song Structure parent crosses Song boundary")
            for row in self._conn.execute(
                "SELECT song_id,current_revision_id FROM song_structure_state"
            ):
                current = self.get_revision(str(row["current_revision_id"]))
                if current is None or current.song_id != str(row["song_id"]):
                    raise LineageCorruptionError("Song Structure current pointer is invalid")
        except LineageCorruptionError:
            raise
        except (sqlite3.DatabaseError, ValueError, TypeError) as exc:
            raise LineageCorruptionError("Song Structure memory is unreadable or corrupt") from exc
