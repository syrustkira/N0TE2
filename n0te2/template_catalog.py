from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from .lineage import LineageCorruptionError, LineageStore, NotFoundError, ValidationError
from .templates import TemplateDefinition, TemplateRole, TemplateValidationError

TEMPLATE_CATALOG_SCHEMA_VERSION = 1
TEMPLATE_SELECTION_SOURCE = "ARTIST_DECLARED"
_MAX_TEMPLATE_ID = 200
_MAX_NAME = 120
_MAX_INTENT = 1000
_MAX_ROLES = 8
_MAX_ROLE_ID = 120
_MAX_CAPABILITY = 200
_MAX_DESCRIPTION = 600
_MAX_TAGS = 8
_MAX_TAG = 80


class TemplateCatalogError(RuntimeError):
    """Invalid or unsafe durable Template catalog operation."""


@dataclass(frozen=True)
class TemplateSelection:
    sequence: int
    id: str
    artist_id: str
    song_id: str
    template_id: str
    source_kind: str


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _bounded(value: str, field: str, maximum: int) -> str:
    text = str(value).strip()
    if not text:
        raise TemplateCatalogError(f"{field} must not be empty")
    if len(text) > maximum:
        raise TemplateCatalogError(f"{field} must be at most {maximum} characters")
    return text


class TemplateCatalog:
    """Durable provider-neutral Template definitions and explicit Song selections.

    The catalog is profile-local and deliberately does not instantiate a host,
    resolve a capability route, start a Session, mutate a Version, or grant action
    authority. Reads against a profile with no catalog remain pure. The bounded
    schema is created only by the first explicit save operation.
    """

    _TABLES = ("template_definitions", "template_selections")
    _TRIGGERS = {
        "template_definition_artist_binding",
        "template_definitions_immutable_update",
        "template_definitions_immutable_delete",
        "template_selection_binding_valid",
        "template_selections_immutable_update",
        "template_selections_immutable_delete",
    }

    def __init__(self, store: LineageStore):
        if not isinstance(store, LineageStore):
            raise TypeError("TemplateCatalog requires the canonical LineageStore")
        self.store = store
        self._conn = store._conn
        # Existing-catalog validation may resolve selection references through
        # get(). Seed the read gate only for that validation pass; the returned
        # value immediately restores False for profiles where no catalog exists.
        self._present = True
        self._present = self._validate_or_absent()

    def _table_exists(self, name: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )

    def _metadata_value(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def _trigger_names(self) -> set[str]:
        return {
            str(row["name"])
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'template_%'"
            )
        }

    @staticmethod
    def _trigger_statements() -> tuple[str, ...]:
        return (
            """CREATE TRIGGER template_definition_artist_binding
            BEFORE INSERT ON template_definitions
            WHEN NEW.artist_id<>(SELECT value FROM metadata WHERE key='primary_artist_id')
            BEGIN SELECT RAISE(ABORT, 'Template artist does not match active profile'); END""",
            """CREATE TRIGGER template_definitions_immutable_update
            BEFORE UPDATE ON template_definitions
            BEGIN SELECT RAISE(ABORT, 'Template definitions are immutable'); END""",
            """CREATE TRIGGER template_definitions_immutable_delete
            BEFORE DELETE ON template_definitions
            BEGIN SELECT RAISE(ABORT, 'Template definitions are immutable'); END""",
            """CREATE TRIGGER template_selection_binding_valid
            BEFORE INSERT ON template_selections
            WHEN NEW.artist_id<>(SELECT value FROM metadata WHERE key='primary_artist_id')
              OR NOT EXISTS (
                  SELECT 1 FROM songs s
                  WHERE s.id=NEW.song_id AND s.artist_id=NEW.artist_id
              )
              OR NOT EXISTS (
                  SELECT 1 FROM template_definitions t
                  WHERE t.id=NEW.template_id AND t.artist_id=NEW.artist_id
              )
            BEGIN SELECT RAISE(ABORT, 'Template selection binding is invalid'); END""",
            """CREATE TRIGGER template_selections_immutable_update
            BEFORE UPDATE ON template_selections
            BEGIN SELECT RAISE(ABORT, 'Template selection history is immutable'); END""",
            """CREATE TRIGGER template_selections_immutable_delete
            BEFORE DELETE ON template_selections
            BEGIN SELECT RAISE(ABORT, 'Template selection history is immutable'); END""",
        )

    def _validate_or_absent(self) -> bool:
        present = [self._table_exists(name) for name in self._TABLES]
        version = self._metadata_value("template_catalog_schema_version")
        if not any(present) and version is None:
            return False
        if not all(present) or version is None:
            raise LineageCorruptionError("Template catalog schema metadata/table mismatch")
        if version != str(TEMPLATE_CATALOG_SCHEMA_VERSION):
            raise LineageCorruptionError(
                f"unsupported Template catalog schema version: {version}"
            )
        missing = self._TRIGGERS - self._trigger_names()
        if missing:
            raise LineageCorruptionError(
                f"Template catalog integrity hooks are incomplete: {sorted(missing)}"
            )
        try:
            for row in self._conn.execute(
                "SELECT seq,id,artist_id,family,name,intent,roles_json "
                "FROM template_definitions ORDER BY seq"
            ):
                if str(row["artist_id"]) != self.store.primary_artist_id:
                    raise LineageCorruptionError(
                        "Template definition artist does not match active profile"
                    )
                self._definition(row)
            for row in self._conn.execute(
                "SELECT seq,id,artist_id,song_id,template_id,source_kind "
                "FROM template_selections ORDER BY seq"
            ):
                selection = self._selection(row)
                song = self.store.get_song(selection.song_id)
                if song is None or song.artist_id != selection.artist_id:
                    raise LineageCorruptionError(
                        "Template selection references an invalid Song"
                    )
                template = self.get(selection.template_id)
                if template is None:
                    raise LineageCorruptionError(
                        "Template selection references a missing Template"
                    )
        except LineageCorruptionError:
            raise
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError("Template catalog is unreadable") from exc
        return True

    def _initialize_schema(self) -> None:
        if self._present:
            return
        if not self._table_exists("songs") or not self._table_exists("artists"):
            raise LineageCorruptionError(
                "Template catalog requires canonical Artist and Song identity first"
            )
        try:
            with self.store._tx():
                self._conn.execute(
                    """CREATE TABLE template_definitions (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        artist_id TEXT NOT NULL REFERENCES artists(id),
                        family TEXT NOT NULL CHECK(length(trim(family)) > 0),
                        name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                        intent TEXT NOT NULL CHECK(length(trim(intent)) > 0),
                        roles_json TEXT NOT NULL CHECK(length(trim(roles_json)) > 0)
                    )"""
                )
                self._conn.execute(
                    """CREATE TABLE template_selections (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        artist_id TEXT NOT NULL REFERENCES artists(id),
                        song_id TEXT NOT NULL REFERENCES songs(id),
                        template_id TEXT NOT NULL REFERENCES template_definitions(id),
                        source_kind TEXT NOT NULL CHECK(source_kind='ARTIST_DECLARED')
                    )"""
                )
                self._conn.execute(
                    "CREATE INDEX template_selections_by_song "
                    "ON template_selections(song_id,seq)"
                )
                for statement in self._trigger_statements():
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT INTO metadata(key,value) VALUES('template_catalog_schema_version',?)",
                    (str(TEMPLATE_CATALOG_SCHEMA_VERSION),),
                )
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError("cannot initialize Template catalog") from exc
        self._present = True

    @staticmethod
    def _roles_json(definition: TemplateDefinition) -> str:
        payload = [
            {
                "role_id": role.role_id,
                "capability": role.capability,
                "description": role.description,
                "required": role.required,
                "tags": list(role.tags),
            }
            for role in definition.roles
        ]
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _definition(row: sqlite3.Row) -> TemplateDefinition:
        try:
            payload = json.loads(str(row["roles_json"]))
            if not isinstance(payload, list) or not payload:
                raise ValueError("roles payload must be a non-empty list")
            roles: list[TemplateRole] = []
            for item in payload:
                if not isinstance(item, dict) or set(item) != {
                    "role_id",
                    "capability",
                    "description",
                    "required",
                    "tags",
                }:
                    raise ValueError("role payload shape is invalid")
                tags = item["tags"]
                if not isinstance(tags, list) or not all(
                    isinstance(tag, str) for tag in tags
                ):
                    raise ValueError("role tags payload is invalid")
                roles.append(
                    TemplateRole(
                        role_id=str(item["role_id"]),
                        capability=str(item["capability"]),
                        description=str(item["description"]),
                        required=item["required"],
                        tags=tuple(tags),
                    )
                )
            return TemplateDefinition(
                template_id=str(row["id"]),
                family=str(row["family"]),
                name=str(row["name"]),
                intent=str(row["intent"]),
                roles=tuple(roles),
            )
        except (TypeError, ValueError, TemplateValidationError, json.JSONDecodeError) as exc:
            raise LineageCorruptionError(
                "Template definition cannot be reconstructed safely"
            ) from exc

    @staticmethod
    def _selection(row: sqlite3.Row) -> TemplateSelection:
        source = str(row["source_kind"])
        if source != TEMPLATE_SELECTION_SOURCE:
            raise LineageCorruptionError("Template selection source is invalid")
        return TemplateSelection(
            sequence=int(row["seq"]),
            id=str(row["id"]),
            artist_id=str(row["artist_id"]),
            song_id=str(row["song_id"]),
            template_id=str(row["template_id"]),
            source_kind=source,
        )

    @staticmethod
    def _validate_definition(definition: TemplateDefinition) -> TemplateDefinition:
        if not isinstance(definition, TemplateDefinition):
            raise TypeError("definition must be TemplateDefinition")
        _bounded(definition.template_id, "template_id", _MAX_TEMPLATE_ID)
        _bounded(definition.name, "template name", _MAX_NAME)
        _bounded(definition.intent, "template intent", _MAX_INTENT)
        if len(definition.roles) > _MAX_ROLES:
            raise TemplateCatalogError(
                f"Template may contain at most {_MAX_ROLES} semantic roles"
            )
        for role in definition.roles:
            _bounded(role.role_id, "role_id", _MAX_ROLE_ID)
            _bounded(role.capability, "role capability", _MAX_CAPABILITY)
            _bounded(role.description, "role description", _MAX_DESCRIPTION)
            if len(role.tags) > _MAX_TAGS:
                raise TemplateCatalogError(
                    f"Template role may contain at most {_MAX_TAGS} tags"
                )
            for tag in role.tags:
                _bounded(tag, "role tag", _MAX_TAG)
        return definition

    def templates(self) -> tuple[TemplateDefinition, ...]:
        if not self._present:
            return ()
        return tuple(
            self._definition(row)
            for row in self._conn.execute(
                "SELECT seq,id,artist_id,family,name,intent,roles_json "
                "FROM template_definitions WHERE artist_id=? ORDER BY seq",
                (self.store.primary_artist_id,),
            )
        )

    def get(self, template_id: str) -> TemplateDefinition | None:
        if not self._present:
            return None
        row = self._conn.execute(
            "SELECT seq,id,artist_id,family,name,intent,roles_json "
            "FROM template_definitions WHERE id=? AND artist_id=?",
            (str(template_id), self.store.primary_artist_id),
        ).fetchone()
        return None if row is None else self._definition(row)

    def save(self, definition: TemplateDefinition) -> TemplateDefinition:
        definition = self._validate_definition(definition)
        self._initialize_schema()
        existing = self.get(definition.template_id)
        if existing is not None:
            if existing == definition:
                return existing
            raise TemplateCatalogError(
                "Template identity already exists with different immutable meaning"
            )
        try:
            with self.store._tx():
                self._conn.execute(
                    "INSERT INTO template_definitions(id,artist_id,family,name,intent,roles_json) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        definition.template_id,
                        self.store.primary_artist_id,
                        definition.family,
                        definition.name,
                        definition.intent,
                        self._roles_json(definition),
                    ),
                )
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError("cannot save Template definition safely") from exc
        return definition

    def create(
        self,
        *,
        family: str,
        name: str,
        intent: str,
        roles: tuple[TemplateRole, ...],
    ) -> TemplateDefinition:
        definition = TemplateDefinition(
            template_id=_new_id("tpl"),
            family=family,
            name=name,
            intent=intent,
            roles=roles,
        )
        return self.save(definition)

    def select_for_song(self, *, song_id: str, template_id: str) -> TemplateSelection:
        song = self.store.get_song(str(song_id))
        if song is None or song.artist_id != self.store.primary_artist_id:
            raise NotFoundError("Song not found in active profile")
        template = self.get(str(template_id))
        if template is None:
            raise NotFoundError("Template not found in active profile")
        selection = TemplateSelection(
            sequence=0,
            id=_new_id("tsel"),
            artist_id=self.store.primary_artist_id,
            song_id=song.id,
            template_id=template.template_id,
            source_kind=TEMPLATE_SELECTION_SOURCE,
        )
        try:
            with self.store._tx():
                cursor = self._conn.execute(
                    "INSERT INTO template_selections("
                    "id,artist_id,song_id,template_id,source_kind) VALUES(?,?,?,?,?)",
                    (
                        selection.id,
                        selection.artist_id,
                        selection.song_id,
                        selection.template_id,
                        selection.source_kind,
                    ),
                )
                sequence = int(cursor.lastrowid)
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError("cannot record Template selection safely") from exc
        return TemplateSelection(
            sequence=sequence,
            id=selection.id,
            artist_id=selection.artist_id,
            song_id=selection.song_id,
            template_id=selection.template_id,
            source_kind=selection.source_kind,
        )

    def selection_history(self, song_id: str) -> tuple[TemplateSelection, ...]:
        song = self.store.get_song(str(song_id))
        if song is None or song.artist_id != self.store.primary_artist_id:
            raise NotFoundError("Song not found in active profile")
        if not self._present:
            return ()
        return tuple(
            self._selection(row)
            for row in self._conn.execute(
                "SELECT seq,id,artist_id,song_id,template_id,source_kind "
                "FROM template_selections WHERE song_id=? AND artist_id=? ORDER BY seq",
                (song.id, self.store.primary_artist_id),
            )
        )

    def current_selection(self, song_id: str) -> TemplateSelection | None:
        history = self.selection_history(song_id)
        return None if not history else history[-1]

    def selected_template(self, song_id: str) -> TemplateDefinition | None:
        selection = self.current_selection(song_id)
        return None if selection is None else self.get(selection.template_id)
