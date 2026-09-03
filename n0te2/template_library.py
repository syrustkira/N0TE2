from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from .lineage import LineageCorruptionError, LineageStore, NotFoundError, ValidationError
from .templates import TemplateDefinition, TemplateRole

TEMPLATE_LIBRARY_SCHEMA_VERSION = 1


class TemplateLibraryError(RuntimeError):
    """A durable provider-neutral Template cannot be saved or selected truthfully."""


@dataclass(frozen=True)
class TemplateSelection:
    sequence: int
    id: str
    artist_id: str
    song_id: str
    template_id: str


class TemplateLibrary:
    """Durable provider-neutral Template definitions and Song selections.

    The library lives inside the canonical profile LineageStore. It remembers
    reusable Template meaning and which Template the artist selected for a Song;
    it does not resolve a StudioCapabilityProfile, instantiate a host, execute a
    provider route, or grant action authority.
    """

    _TRIGGER_NAMES = {
        "template_definition_immutable_update",
        "template_definition_immutable_delete",
        "template_role_immutable_update",
        "template_role_immutable_delete",
        "template_role_same_artist",
        "template_selection_same_artist",
        "template_selection_immutable_update",
        "template_selection_immutable_delete",
        "template_saved_activity",
        "template_selected_activity",
    }

    def __init__(self, store: LineageStore):
        if not isinstance(store, LineageStore):
            raise TypeError("TemplateLibrary requires the canonical LineageStore")
        self.store = store
        self._conn = store._conn
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

    def _ensure_schema(self) -> None:
        names = ("template_definitions", "template_roles", "song_template_selections")
        tables = {name: self._table_exists(name) for name in names}
        version = self._metadata_value("template_library_schema_version")
        if any(tables.values()) or version is not None:
            if not all(tables.values()) or version != str(TEMPLATE_LIBRARY_SCHEMA_VERSION):
                raise LineageCorruptionError("Template library schema metadata/table mismatch")
            return
        if not self._table_exists("activity_events"):
            raise LineageCorruptionError(
                "TemplateLibrary requires canonical Activity chronology first"
            )
        try:
            with self.store._tx():
                self._conn.execute(
                    """CREATE TABLE template_definitions (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        template_id TEXT NOT NULL UNIQUE,
                        artist_id TEXT NOT NULL REFERENCES artists(id),
                        family TEXT NOT NULL,
                        name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                        intent TEXT NOT NULL CHECK(length(trim(intent)) > 0)
                    )"""
                )
                self._conn.execute(
                    """CREATE TABLE template_roles (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        template_id TEXT NOT NULL REFERENCES template_definitions(template_id),
                        role_id TEXT NOT NULL,
                        capability TEXT NOT NULL CHECK(length(trim(capability)) > 0),
                        description TEXT NOT NULL CHECK(length(trim(description)) > 0),
                        required INTEGER NOT NULL CHECK(required IN (0,1)),
                        tags_json TEXT NOT NULL,
                        UNIQUE(template_id, role_id)
                    )"""
                )
                self._conn.execute(
                    """CREATE TABLE song_template_selections (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        artist_id TEXT NOT NULL REFERENCES artists(id),
                        song_id TEXT NOT NULL REFERENCES songs(id),
                        template_id TEXT NOT NULL REFERENCES template_definitions(template_id)
                    )"""
                )
                self._conn.execute(
                    "CREATE INDEX template_selection_by_song "
                    "ON song_template_selections(song_id,seq)"
                )
                for statement in self._trigger_statements():
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT INTO metadata(key,value) VALUES('template_library_schema_version',?)",
                    (str(TEMPLATE_LIBRARY_SCHEMA_VERSION),),
                )
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError("cannot initialize Template library") from exc

    @staticmethod
    def _trigger_statements() -> tuple[str, ...]:
        return (
            """CREATE TRIGGER template_definition_immutable_update
            BEFORE UPDATE ON template_definitions BEGIN
                SELECT RAISE(ABORT, 'Template definition is immutable');
            END""",
            """CREATE TRIGGER template_definition_immutable_delete
            BEFORE DELETE ON template_definitions BEGIN
                SELECT RAISE(ABORT, 'Template definition history is immutable');
            END""",
            """CREATE TRIGGER template_role_immutable_update
            BEFORE UPDATE ON template_roles BEGIN
                SELECT RAISE(ABORT, 'Template role is immutable');
            END""",
            """CREATE TRIGGER template_role_immutable_delete
            BEFORE DELETE ON template_roles BEGIN
                SELECT RAISE(ABORT, 'Template role history is immutable');
            END""",
            """CREATE TRIGGER template_role_same_artist
            BEFORE INSERT ON template_roles
            WHEN NOT EXISTS (
                SELECT 1 FROM template_definitions t
                WHERE t.template_id=NEW.template_id
            ) BEGIN
                SELECT RAISE(ABORT, 'Template role lost its definition');
            END""",
            """CREATE TRIGGER template_selection_same_artist
            BEFORE INSERT ON song_template_selections
            WHEN NOT EXISTS (
                SELECT 1 FROM songs s
                JOIN template_definitions t ON t.template_id=NEW.template_id
                WHERE s.id=NEW.song_id
                  AND s.artist_id=NEW.artist_id
                  AND t.artist_id=NEW.artist_id
            ) BEGIN
                SELECT RAISE(ABORT, 'Template selection crosses Artist or Song boundary');
            END""",
            """CREATE TRIGGER template_selection_immutable_update
            BEFORE UPDATE ON song_template_selections BEGIN
                SELECT RAISE(ABORT, 'Template selection history is immutable');
            END""",
            """CREATE TRIGGER template_selection_immutable_delete
            BEFORE DELETE ON song_template_selections BEGIN
                SELECT RAISE(ABORT, 'Template selection history is immutable');
            END""",
            """CREATE TRIGGER template_saved_activity
            AFTER INSERT ON template_definitions
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,object_type,object_id,payload_json
                ) VALUES(
                    'act_'||lower(hex(randomblob(16))),
                    'TEMPLATE_SAVED',NEW.artist_id,NULL,NULL,
                    'TEMPLATE',NEW.template_id,
                    '{\"family\":\"'||replace(NEW.family,'\"','')||'\"}'
                );
            END""",
            """CREATE TRIGGER template_selected_activity
            AFTER INSERT ON song_template_selections
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,object_type,object_id,payload_json
                ) VALUES(
                    'act_'||lower(hex(randomblob(16))),
                    'TEMPLATE_SELECTED',NEW.artist_id,NEW.song_id,NULL,
                    'TEMPLATE',NEW.template_id,'{}'
                );
            END""",
        )

    @staticmethod
    def _selection(row: sqlite3.Row) -> TemplateSelection:
        return TemplateSelection(
            sequence=int(row["seq"]),
            id=str(row["id"]),
            artist_id=str(row["artist_id"]),
            song_id=str(row["song_id"]),
            template_id=str(row["template_id"]),
        )

    @staticmethod
    def _tags_json(tags: tuple[str, ...]) -> str:
        return json.dumps(list(tags), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _tags(value: str) -> tuple[str, ...]:
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError as exc:
            raise LineageCorruptionError("Template role tags are corrupt") from exc
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise LineageCorruptionError("Template role tags are not a string list")
        return tuple(parsed)

    def _definition(self, template_id: str) -> TemplateDefinition | None:
        row = self._conn.execute(
            "SELECT seq,template_id,artist_id,family,name,intent "
            "FROM template_definitions WHERE template_id=?",
            (str(template_id),),
        ).fetchone()
        if row is None:
            return None
        if str(row["artist_id"]) != self.store.primary_artist_id:
            raise LineageCorruptionError("Template definition artist does not match profile")
        roles = tuple(
            TemplateRole(
                role_id=str(role["role_id"]),
                capability=str(role["capability"]),
                description=str(role["description"]),
                required=bool(int(role["required"])),
                tags=self._tags(str(role["tags_json"])),
            )
            for role in self._conn.execute(
                "SELECT role_id,capability,description,required,tags_json "
                "FROM template_roles WHERE template_id=? ORDER BY role_id",
                (str(template_id),),
            )
        )
        if not roles:
            raise LineageCorruptionError("Template definition has no semantic roles")
        try:
            return TemplateDefinition(
                template_id=str(row["template_id"]),
                family=str(row["family"]),
                name=str(row["name"]),
                intent=str(row["intent"]),
                roles=roles,
            )
        except (TypeError, ValueError) as exc:
            raise LineageCorruptionError("Template definition cannot be reconstructed") from exc

    def _validate_existing(self) -> None:
        try:
            if self._metadata_value("template_library_schema_version") != str(
                TEMPLATE_LIBRARY_SCHEMA_VERSION
            ):
                raise LineageCorruptionError("unsupported Template library schema version")
            trigger_names = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='trigger' AND name LIKE 'template_%'"
                )
            }
            missing = self._TRIGGER_NAMES - trigger_names
            if missing:
                raise LineageCorruptionError(
                    f"Template library integrity hooks are incomplete: {sorted(missing)}"
                )
            template_ids = [
                str(row["template_id"])
                for row in self._conn.execute(
                    "SELECT template_id FROM template_definitions ORDER BY seq"
                )
            ]
            for template_id in template_ids:
                if self._definition(template_id) is None:
                    raise LineageCorruptionError("Template definition disappeared")
            for row in self._conn.execute(
                "SELECT seq,id,artist_id,song_id,template_id "
                "FROM song_template_selections ORDER BY seq"
            ):
                selection = self._selection(row)
                if selection.artist_id != self.store.primary_artist_id:
                    raise LineageCorruptionError("Template selection artist does not match profile")
                song = self.store.get_song(selection.song_id)
                if song is None or song.artist_id != selection.artist_id:
                    raise LineageCorruptionError("Template selection is bound to invalid Song")
                if self._definition(selection.template_id) is None:
                    raise LineageCorruptionError("Template selection lost its definition")
        except LineageCorruptionError:
            raise
        except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
            raise LineageCorruptionError("Template library is unreadable or corrupt") from exc

    def get(self, template_id: str) -> TemplateDefinition | None:
        return self._definition(str(template_id))

    def all(self) -> tuple[TemplateDefinition, ...]:
        return tuple(
            definition
            for row in self._conn.execute(
                "SELECT template_id FROM template_definitions ORDER BY seq"
            )
            if (definition := self._definition(str(row["template_id"]))) is not None
        )

    def save(self, template: TemplateDefinition) -> TemplateDefinition:
        if not isinstance(template, TemplateDefinition):
            raise TypeError("template must be TemplateDefinition")
        existing = self._definition(template.template_id)
        if existing is not None:
            if existing == template:
                return existing
            raise ValidationError(
                "Template identity already belongs to a different immutable definition"
            )
        try:
            with self.store._tx():
                self._conn.execute(
                    "INSERT INTO template_definitions("
                    "template_id,artist_id,family,name,intent) VALUES(?,?,?,?,?)",
                    (
                        template.template_id,
                        self.store.primary_artist_id,
                        template.family,
                        template.name,
                        template.intent,
                    ),
                )
                for role in template.roles:
                    self._conn.execute(
                        "INSERT INTO template_roles("
                        "template_id,role_id,capability,description,required,tags_json) "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            template.template_id,
                            role.role_id,
                            role.capability,
                            role.description,
                            1 if role.required else 0,
                            self._tags_json(role.tags),
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"cannot save Template: {exc}") from exc
        saved = self._definition(template.template_id)
        if saved != template:
            raise LineageCorruptionError("saved Template did not reconstruct exactly")
        return saved

    def selected_for_song(self, song_id: str) -> TemplateSelection | None:
        song = self.store.get_song(str(song_id))
        if song is None:
            raise NotFoundError(
                f"Song not found in profile {self.store.profile_id}: {song_id}"
            )
        row = self._conn.execute(
            "SELECT seq,id,artist_id,song_id,template_id "
            "FROM song_template_selections WHERE song_id=? ORDER BY seq DESC LIMIT 1",
            (song.id,),
        ).fetchone()
        return None if row is None else self._selection(row)

    def select_for_song(self, song_id: str, template_id: str) -> TemplateSelection:
        song = self.store.get_song(str(song_id))
        if song is None:
            raise NotFoundError(
                f"Song not found in profile {self.store.profile_id}: {song_id}"
            )
        template = self._definition(str(template_id))
        if template is None:
            raise NotFoundError(
                f"Template not found in profile {self.store.profile_id}: {template_id}"
            )
        current = self.selected_for_song(song.id)
        if current is not None and current.template_id == template.template_id:
            return current
        selection_id = f"tsel_{uuid.uuid4().hex}"
        try:
            with self.store._tx():
                self._conn.execute(
                    "INSERT INTO song_template_selections("
                    "id,artist_id,song_id,template_id) VALUES(?,?,?,?)",
                    (
                        selection_id,
                        self.store.primary_artist_id,
                        song.id,
                        template.template_id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"cannot select Template: {exc}") from exc
        selected = self.selected_for_song(song.id)
        if selected is None or selected.id != selection_id:
            raise LineageCorruptionError("new Template selection did not become current")
        return selected

    def selection_history(self, song_id: str) -> tuple[TemplateSelection, ...]:
        song = self.store.get_song(str(song_id))
        if song is None:
            raise NotFoundError(
                f"Song not found in profile {self.store.profile_id}: {song_id}"
            )
        return tuple(
            self._selection(row)
            for row in self._conn.execute(
                "SELECT seq,id,artist_id,song_id,template_id "
                "FROM song_template_selections WHERE song_id=? ORDER BY seq",
                (song.id,),
            )
        )
