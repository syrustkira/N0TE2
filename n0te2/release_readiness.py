from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from .lineage import LineageCorruptionError, LineageStore, NotFoundError, ValidationError

RELEASE_READINESS_SCHEMA_VERSION = 1
PLAN_STATES = ("ACTIVE", "ARCHIVED")
DELIVERABLE_KINDS = (
    "MASTER_FILE",
    "COVER_ART",
    "METADATA",
    "RIGHTS_CREDITS",
    "CAMPAIGN_ASSET",
    "PITCH_ASSET",
    "DIRECT_FAN_ASSET",
    "OTHER",
)
DELIVERABLE_STATES = ("UNKNOWN", "MISSING", "READY", "BLOCKED", "NOT_REQUIRED")
MILESTONE_STATES = ("OPEN", "DONE", "BLOCKED", "NOT_REQUIRED")
APPROVED_VERSION_STATES = ("PRESENT", "MISSING")
REVIEW_STATES = ("BLOCKED", "MISSING", "UNKNOWN", "IN_PROGRESS", "READY_FOR_REVIEW")
RELEASE_AUTHORITY = "PLANNING_ONLY"


class ReleaseReadinessError(RuntimeError):
    """Release readiness cannot be represented without weakening its truth boundary."""


class StaleReleasePlanError(ReleaseReadinessError):
    """The exact release-plan state changed after an action was prepared."""


@dataclass(frozen=True)
class ReleasePlan:
    sequence: int
    id: str
    artist_id: str
    song_id: str
    target_on: str
    state: str
    archived_note: str | None

    @property
    def provider_date_verified(self) -> bool:
        return False

    @property
    def release_authority_granted(self) -> bool:
        return False

    @property
    def external_action_authority_granted(self) -> bool:
        return False


@dataclass(frozen=True)
class ReleaseDeliverable:
    sequence: int
    id: str
    plan_id: str
    artist_id: str
    song_id: str
    kind: str
    label: str
    required: bool
    state: str
    state_sequence: int
    note: str | None

    @property
    def provider_accepted(self) -> bool:
        return False

    @property
    def independently_verified(self) -> bool:
        return False

    @property
    def legal_clearance_verified(self) -> bool:
        return False

    @property
    def external_action_authority_granted(self) -> bool:
        return False


@dataclass(frozen=True)
class ReleaseMilestone:
    sequence: int
    id: str
    plan_id: str
    artist_id: str
    song_id: str
    label: str
    lead_days: int
    target_on: str
    due_on: str
    state: str
    state_sequence: int
    note: str | None

    @property
    def calendar_authority_granted(self) -> bool:
        return False

    @property
    def external_action_authority_granted(self) -> bool:
        return False


@dataclass(frozen=True)
class ReleaseReadinessSnapshot:
    plan: ReleasePlan
    approved_version_id: str | None
    approved_version_state: str
    deliverables: tuple[ReleaseDeliverable, ...]
    milestones: tuple[ReleaseMilestone, ...]
    review_state: str
    unresolved: tuple[str, ...]
    authority: str = RELEASE_AUTHORITY

    @property
    def provider_release_scheduled(self) -> bool:
        return False

    @property
    def distribution_uploaded(self) -> bool:
        return False

    @property
    def campaign_sent(self) -> bool:
        return False

    @property
    def pitch_submitted(self) -> bool:
        return False

    @property
    def spend_authorized(self) -> bool:
        return False

    @property
    def legal_clearance_verified(self) -> bool:
        return False

    @property
    def external_action_authority_granted(self) -> bool:
        return False

    @property
    def priority_score(self) -> None:
        return None


@dataclass(frozen=True)
class PlanBinding:
    plan_id: str
    song_id: str
    expected_state: str
    expected_revision: str


@dataclass(frozen=True)
class DeliverableBinding:
    deliverable_id: str
    plan_id: str
    expected_plan_revision: str
    expected_state_sequence: int
    expected_state: str


@dataclass(frozen=True)
class MilestoneBinding:
    milestone_id: str
    plan_id: str
    expected_plan_revision: str
    expected_state_sequence: int
    expected_state: str


def _required_text(value: object, field_name: str, *, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be text")
    text = " ".join(value.split())
    if not text:
        raise ValidationError(f"{field_name} must not be empty")
    if len(text) > maximum:
        raise ValidationError(f"{field_name} is too long")
    return text


def _optional_text(
    value: object | None,
    field_name: str,
    *,
    maximum: int = 2000,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be text")
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) > maximum:
        raise ValidationError(f"{field_name} is too long")
    return text


def _enum_text(value: object, field_name: str, allowed: tuple[str, ...]) -> str:
    text = (
        _required_text(value, field_name, maximum=64)
        .upper()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if text not in allowed:
        raise ValidationError(f"unsupported {field_name}: {text}")
    return text


def _iso_date(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be an ISO calendar date (YYYY-MM-DD)")
    text = value.strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be an ISO calendar date (YYYY-MM-DD)") from exc


def _whole_days(value: object) -> int:
    if type(value) is not int:
        raise ValidationError("lead_days must be a whole number of days")
    if value < 0 or value > 730:
        raise ValidationError("lead_days must be between 0 and 730")
    return value


class ReleaseReadinessMemory:
    """Song-bound local release planning inside the canonical profile database.

    This layer owns planning chronology only. It does not duplicate Song approval,
    Rights, Credits, obligations, Direct Fan consent, provider delivery, or external
    execution. Artist-entered READY states remain local declarations and are never
    transformed into provider acceptance or legal clearance.
    """

    _TRIGGER_NAMES = {
        "release_plan_binding_immutable",
        "release_plan_transition_shape",
        "release_plan_delete_immutable",
        "release_plan_song_same_artist",
        "release_deliverable_binding_immutable",
        "release_deliverable_delete_immutable",
        "release_deliverable_plan_binding",
        "release_deliverable_event_immutable_update",
        "release_deliverable_event_immutable_delete",
        "release_deliverable_event_active_plan",
        "release_deliverable_event_requirement",
        "release_milestone_binding_immutable",
        "release_milestone_delete_immutable",
        "release_milestone_plan_binding",
        "release_milestone_event_immutable_update",
        "release_milestone_event_immutable_delete",
        "release_milestone_event_active_plan",
        "release_plan_created_activity",
        "release_plan_archived_activity",
        "release_deliverable_state_activity",
        "release_milestone_state_activity",
    }

    def __init__(self, store: LineageStore) -> None:
        if not isinstance(store, LineageStore):
            raise TypeError("ReleaseReadinessMemory requires the canonical LineageStore")
        self.store = store
        self._conn = store._conn
        self._ensure_schema()
        self._validate_existing()

    def _table_exists(self, name: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone() is not None

    def _metadata_value(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM metadata WHERE key=?",
            (key,),
        ).fetchone()
        return None if row is None else str(row["value"])

    @staticmethod
    def _trigger_statements() -> tuple[str, ...]:
        return (
            """CREATE TRIGGER release_plan_binding_immutable
            BEFORE UPDATE OF id,artist_id,song_id,target_on ON release_plans
            BEGIN
                SELECT RAISE(ABORT,'release plan identity, Song, and target date are immutable');
            END""",
            """CREATE TRIGGER release_plan_transition_shape
            BEFORE UPDATE OF state,archived_note ON release_plans
            BEGIN
                SELECT CASE
                    WHEN OLD.state='ACTIVE' AND NEW.state='ARCHIVED'
                         AND NEW.archived_note IS NOT NULL
                         AND length(trim(NEW.archived_note))>0
                    THEN NULL
                    ELSE RAISE(ABORT,'release plan may only archive once with a reason')
                END;
            END""",
            """CREATE TRIGGER release_plan_delete_immutable
            BEFORE DELETE ON release_plans
            BEGIN
                SELECT RAISE(ABORT,'release plan history is immutable');
            END""",
            """CREATE TRIGGER release_plan_song_same_artist
            BEFORE INSERT ON release_plans
            BEGIN
                SELECT CASE WHEN EXISTS(
                    SELECT 1 FROM songs s
                    WHERE s.id=NEW.song_id AND s.artist_id=NEW.artist_id
                ) THEN NULL ELSE RAISE(ABORT,'release plan Song binding is invalid') END;
            END""",
            """CREATE TRIGGER release_deliverable_binding_immutable
            BEFORE UPDATE ON release_deliverables
            BEGIN
                SELECT RAISE(ABORT,'release deliverable definition is immutable');
            END""",
            """CREATE TRIGGER release_deliverable_delete_immutable
            BEFORE DELETE ON release_deliverables
            BEGIN
                SELECT RAISE(ABORT,'release deliverable history is immutable');
            END""",
            """CREATE TRIGGER release_deliverable_plan_binding
            BEFORE INSERT ON release_deliverables
            BEGIN
                SELECT CASE WHEN EXISTS(
                    SELECT 1 FROM release_plans p
                    WHERE p.id=NEW.plan_id
                      AND p.artist_id=NEW.artist_id
                      AND p.song_id=NEW.song_id
                      AND p.state='ACTIVE'
                ) THEN NULL ELSE RAISE(ABORT,'release deliverable plan binding is invalid') END;
            END""",
            """CREATE TRIGGER release_deliverable_event_immutable_update
            BEFORE UPDATE ON release_deliverable_events
            BEGIN
                SELECT RAISE(ABORT,'release deliverable state history is immutable');
            END""",
            """CREATE TRIGGER release_deliverable_event_immutable_delete
            BEFORE DELETE ON release_deliverable_events
            BEGIN
                SELECT RAISE(ABORT,'release deliverable state history is immutable');
            END""",
            """CREATE TRIGGER release_deliverable_event_active_plan
            BEFORE INSERT ON release_deliverable_events
            BEGIN
                SELECT CASE WHEN EXISTS(
                    SELECT 1
                    FROM release_deliverables d
                    JOIN release_plans p ON p.id=d.plan_id
                    WHERE d.id=NEW.deliverable_id AND p.state='ACTIVE'
                ) THEN NULL ELSE RAISE(ABORT,'release deliverable plan is no longer active') END;
            END""",
            """CREATE TRIGGER release_deliverable_event_requirement
            BEFORE INSERT ON release_deliverable_events
            WHEN NEW.state='NOT_REQUIRED'
            BEGIN
                SELECT CASE WHEN EXISTS(
                    SELECT 1 FROM release_deliverables d
                    WHERE d.id=NEW.deliverable_id AND d.required=0
                ) THEN NULL ELSE RAISE(ABORT,'required release deliverable cannot become NOT_REQUIRED') END;
            END""",
            """CREATE TRIGGER release_milestone_binding_immutable
            BEFORE UPDATE ON release_milestones
            BEGIN
                SELECT RAISE(ABORT,'release milestone definition is immutable');
            END""",
            """CREATE TRIGGER release_milestone_delete_immutable
            BEFORE DELETE ON release_milestones
            BEGIN
                SELECT RAISE(ABORT,'release milestone history is immutable');
            END""",
            """CREATE TRIGGER release_milestone_plan_binding
            BEFORE INSERT ON release_milestones
            BEGIN
                SELECT CASE WHEN EXISTS(
                    SELECT 1 FROM release_plans p
                    WHERE p.id=NEW.plan_id
                      AND p.artist_id=NEW.artist_id
                      AND p.song_id=NEW.song_id
                      AND p.state='ACTIVE'
                ) THEN NULL ELSE RAISE(ABORT,'release milestone plan binding is invalid') END;
                SELECT CASE WHEN NEW.target_on=(
                    SELECT p.target_on FROM release_plans p WHERE p.id=NEW.plan_id
                ) THEN NULL ELSE RAISE(ABORT,'release milestone target date drifted from plan') END;
            END""",
            """CREATE TRIGGER release_milestone_event_immutable_update
            BEFORE UPDATE ON release_milestone_events
            BEGIN
                SELECT RAISE(ABORT,'release milestone state history is immutable');
            END""",
            """CREATE TRIGGER release_milestone_event_immutable_delete
            BEFORE DELETE ON release_milestone_events
            BEGIN
                SELECT RAISE(ABORT,'release milestone state history is immutable');
            END""",
            """CREATE TRIGGER release_milestone_event_active_plan
            BEFORE INSERT ON release_milestone_events
            BEGIN
                SELECT CASE WHEN EXISTS(
                    SELECT 1
                    FROM release_milestones m
                    JOIN release_plans p ON p.id=m.plan_id
                    WHERE m.id=NEW.milestone_id AND p.state='ACTIVE'
                ) THEN NULL ELSE RAISE(ABORT,'release milestone plan is no longer active') END;
            END""",
            """CREATE TRIGGER release_plan_created_activity
            AFTER INSERT ON release_plans
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,
                    object_type,object_id,payload_json
                ) VALUES(
                    'act_'||lower(hex(randomblob(16))),
                    'RELEASE_PLAN_CREATED',NEW.artist_id,NEW.song_id,NULL,
                    'RELEASE_PLAN',NEW.id,'{}'
                );
            END""",
            """CREATE TRIGGER release_plan_archived_activity
            AFTER UPDATE OF state ON release_plans
            WHEN OLD.state='ACTIVE' AND NEW.state='ARCHIVED'
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,
                    object_type,object_id,payload_json
                ) VALUES(
                    'act_'||lower(hex(randomblob(16))),
                    'RELEASE_PLAN_ARCHIVED',NEW.artist_id,NEW.song_id,NULL,
                    'RELEASE_PLAN',NEW.id,'{}'
                );
            END""",
            """CREATE TRIGGER release_deliverable_state_activity
            AFTER INSERT ON release_deliverable_events
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,
                    object_type,object_id,payload_json
                ) SELECT
                    'act_'||lower(hex(randomblob(16))),
                    'RELEASE_DELIVERABLE_STATE',d.artist_id,d.song_id,NULL,
                    'RELEASE_DELIVERABLE',d.id,'{}'
                FROM release_deliverables d WHERE d.id=NEW.deliverable_id;
            END""",
            """CREATE TRIGGER release_milestone_state_activity
            AFTER INSERT ON release_milestone_events
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,
                    object_type,object_id,payload_json
                ) SELECT
                    'act_'||lower(hex(randomblob(16))),
                    'RELEASE_MILESTONE_STATE',m.artist_id,m.song_id,NULL,
                    'RELEASE_MILESTONE',m.id,'{}'
                FROM release_milestones m WHERE m.id=NEW.milestone_id;
            END""",
        )

    def _ensure_schema(self) -> None:
        tables = {
            "release_plans",
            "release_deliverables",
            "release_deliverable_events",
            "release_milestones",
            "release_milestone_events",
        }
        existing = {name for name in tables if self._table_exists(name)}
        version = self._metadata_value("release_readiness_schema_version")
        if existing or version is not None:
            if existing != tables or version != str(RELEASE_READINESS_SCHEMA_VERSION):
                raise LineageCorruptionError(
                    "Release readiness schema metadata/table mismatch"
                )
            return
        if not self._table_exists("activity_events"):
            raise LineageCorruptionError(
                "ReleaseReadinessMemory requires canonical Activity chronology first"
            )
        try:
            with self.store._tx():
                self._conn.execute(
                    """CREATE TABLE release_plans (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        artist_id TEXT NOT NULL REFERENCES artists(id),
                        song_id TEXT NOT NULL REFERENCES songs(id),
                        target_on TEXT NOT NULL CHECK(length(target_on)=10),
                        state TEXT NOT NULL DEFAULT 'ACTIVE'
                            CHECK(state IN ('ACTIVE','ARCHIVED')),
                        archived_note TEXT NULL CHECK(
                            archived_note IS NULL OR length(trim(archived_note))>0
                        )
                    )"""
                )
                self._conn.execute(
                    "CREATE UNIQUE INDEX release_one_active_plan_per_song "
                    "ON release_plans(song_id) WHERE state='ACTIVE'"
                )
                self._conn.execute(
                    """CREATE TABLE release_deliverables (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        plan_id TEXT NOT NULL REFERENCES release_plans(id),
                        artist_id TEXT NOT NULL REFERENCES artists(id),
                        song_id TEXT NOT NULL REFERENCES songs(id),
                        kind TEXT NOT NULL CHECK(kind IN (
                            'MASTER_FILE','COVER_ART','METADATA','RIGHTS_CREDITS',
                            'CAMPAIGN_ASSET','PITCH_ASSET','DIRECT_FAN_ASSET','OTHER'
                        )),
                        label TEXT NOT NULL CHECK(length(trim(label))>0),
                        required INTEGER NOT NULL CHECK(required IN (0,1))
                    )"""
                )
                self._conn.execute(
                    """CREATE TABLE release_deliverable_events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        deliverable_id TEXT NOT NULL REFERENCES release_deliverables(id),
                        state TEXT NOT NULL CHECK(state IN (
                            'UNKNOWN','MISSING','READY','BLOCKED','NOT_REQUIRED'
                        )),
                        note TEXT NULL CHECK(note IS NULL OR length(trim(note))>0)
                    )"""
                )
                self._conn.execute(
                    "CREATE INDEX release_deliverable_events_by_item "
                    "ON release_deliverable_events(deliverable_id,seq)"
                )
                self._conn.execute(
                    """CREATE TABLE release_milestones (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        plan_id TEXT NOT NULL REFERENCES release_plans(id),
                        artist_id TEXT NOT NULL REFERENCES artists(id),
                        song_id TEXT NOT NULL REFERENCES songs(id),
                        label TEXT NOT NULL CHECK(length(trim(label))>0),
                        lead_days INTEGER NOT NULL CHECK(lead_days BETWEEN 0 AND 730),
                        target_on TEXT NOT NULL CHECK(length(target_on)=10),
                        due_on TEXT NOT NULL CHECK(length(due_on)=10)
                    )"""
                )
                self._conn.execute(
                    """CREATE TABLE release_milestone_events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        milestone_id TEXT NOT NULL REFERENCES release_milestones(id),
                        state TEXT NOT NULL CHECK(state IN (
                            'OPEN','DONE','BLOCKED','NOT_REQUIRED'
                        )),
                        note TEXT NULL CHECK(note IS NULL OR length(trim(note))>0)
                    )"""
                )
                self._conn.execute(
                    "CREATE INDEX release_milestone_events_by_item "
                    "ON release_milestone_events(milestone_id,seq)"
                )
                for statement in self._trigger_statements():
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT INTO metadata(key,value) "
                    "VALUES('release_readiness_schema_version',?)",
                    (str(RELEASE_READINESS_SCHEMA_VERSION),),
                )
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError(
                "cannot initialize Release readiness memory"
            ) from exc

    @staticmethod
    def _plan(row: sqlite3.Row) -> ReleasePlan:
        return ReleasePlan(
            sequence=int(row["seq"]),
            id=str(row["id"]),
            artist_id=str(row["artist_id"]),
            song_id=str(row["song_id"]),
            target_on=str(row["target_on"]),
            state=str(row["state"]),
            archived_note=(
                None if row["archived_note"] is None else str(row["archived_note"])
            ),
        )

    def _deliverable(self, row: sqlite3.Row) -> ReleaseDeliverable:
        event = self._conn.execute(
            "SELECT seq,state,note FROM release_deliverable_events "
            "WHERE deliverable_id=? ORDER BY seq DESC LIMIT 1",
            (str(row["id"]),),
        ).fetchone()
        if event is None:
            raise LineageCorruptionError("release deliverable has no state history")
        return ReleaseDeliverable(
            sequence=int(row["seq"]),
            id=str(row["id"]),
            plan_id=str(row["plan_id"]),
            artist_id=str(row["artist_id"]),
            song_id=str(row["song_id"]),
            kind=str(row["kind"]),
            label=str(row["label"]),
            required=bool(row["required"]),
            state=str(event["state"]),
            state_sequence=int(event["seq"]),
            note=None if event["note"] is None else str(event["note"]),
        )

    def _milestone(self, row: sqlite3.Row) -> ReleaseMilestone:
        event = self._conn.execute(
            "SELECT seq,state,note FROM release_milestone_events "
            "WHERE milestone_id=? ORDER BY seq DESC LIMIT 1",
            (str(row["id"]),),
        ).fetchone()
        if event is None:
            raise LineageCorruptionError("release milestone has no state history")
        return ReleaseMilestone(
            sequence=int(row["seq"]),
            id=str(row["id"]),
            plan_id=str(row["plan_id"]),
            artist_id=str(row["artist_id"]),
            song_id=str(row["song_id"]),
            label=str(row["label"]),
            lead_days=int(row["lead_days"]),
            target_on=str(row["target_on"]),
            due_on=str(row["due_on"]),
            state=str(event["state"]),
            state_sequence=int(event["seq"]),
            note=None if event["note"] is None else str(event["note"]),
        )

    def _plan_revision(self, plan_id: str) -> str:
        plan = self.get_plan(plan_id)
        if plan is None:
            raise NotFoundError("release plan not found")
        values: list[int] = []
        for sql, args in (
            (
                "SELECT COALESCE(MAX(seq),0) AS value FROM release_deliverables "
                "WHERE plan_id=?",
                (plan.id,),
            ),
            (
                "SELECT COALESCE(MAX(e.seq),0) AS value "
                "FROM release_deliverable_events e "
                "JOIN release_deliverables d ON d.id=e.deliverable_id "
                "WHERE d.plan_id=?",
                (plan.id,),
            ),
            (
                "SELECT COALESCE(MAX(seq),0) AS value FROM release_milestones "
                "WHERE plan_id=?",
                (plan.id,),
            ),
            (
                "SELECT COALESCE(MAX(e.seq),0) AS value "
                "FROM release_milestone_events e "
                "JOIN release_milestones m ON m.id=e.milestone_id "
                "WHERE m.plan_id=?",
                (plan.id,),
            ),
        ):
            row = self._conn.execute(sql, args).fetchone()
            if row is None:
                raise LineageCorruptionError("release plan revision query failed")
            values.append(int(row["value"]))
        return ":".join((plan.state, *(str(value) for value in values)))

    def _require_active_binding(self, binding: PlanBinding) -> ReleasePlan:
        if not isinstance(binding, PlanBinding):
            raise TypeError("binding must be PlanBinding")
        plan = self.get_plan(binding.plan_id)
        if plan is None or plan.song_id != binding.song_id:
            raise StaleReleasePlanError("release plan binding is no longer valid")
        if plan.state != binding.expected_state or plan.state != "ACTIVE":
            raise StaleReleasePlanError("release plan is no longer active")
        if self._plan_revision(plan.id) != binding.expected_revision:
            raise StaleReleasePlanError(
                "release plan changed after this action was prepared"
            )
        return plan

    def _validate_existing(self) -> None:
        try:
            if self._metadata_value("release_readiness_schema_version") != str(
                RELEASE_READINESS_SCHEMA_VERSION
            ):
                raise LineageCorruptionError(
                    "unsupported Release readiness schema version"
                )
            trigger_names = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='trigger' AND name LIKE 'release_%'"
                )
            }
            missing = self._TRIGGER_NAMES - trigger_names
            if missing:
                raise LineageCorruptionError(
                    f"Release readiness integrity hooks are incomplete: {sorted(missing)}"
                )

            active_by_song: set[str] = set()
            for row in self._conn.execute(
                "SELECT seq,id,artist_id,song_id,target_on,state,archived_note "
                "FROM release_plans ORDER BY seq"
            ):
                plan = self._plan(row)
                if plan.artist_id != self.store.primary_artist_id:
                    raise LineageCorruptionError(
                        "release plan Artist does not match active profile"
                    )
                song = self.store.get_song(plan.song_id)
                if song is None or song.artist_id != plan.artist_id:
                    raise LineageCorruptionError(
                        "release plan Song binding is invalid"
                    )
                _iso_date(plan.target_on, "target_on")
                if plan.state not in PLAN_STATES:
                    raise LineageCorruptionError("release plan state is invalid")
                if plan.state == "ACTIVE":
                    if plan.archived_note is not None:
                        raise LineageCorruptionError(
                            "active release plan has archive note"
                        )
                    if plan.song_id in active_by_song:
                        raise LineageCorruptionError(
                            "multiple active release plans exist for one Song"
                        )
                    active_by_song.add(plan.song_id)
                elif plan.archived_note is None:
                    raise LineageCorruptionError(
                        "archived release plan is missing reason"
                    )

            for row in self._conn.execute(
                "SELECT seq,id,plan_id,artist_id,song_id,kind,label,required "
                "FROM release_deliverables ORDER BY seq"
            ):
                item = self._deliverable(row)
                plan = self.get_plan(item.plan_id)
                if (
                    plan is None
                    or item.artist_id != plan.artist_id
                    or item.song_id != plan.song_id
                ):
                    raise LineageCorruptionError(
                        "release deliverable binding is invalid"
                    )
                if item.kind not in DELIVERABLE_KINDS:
                    raise LineageCorruptionError(
                        "release deliverable kind is invalid"
                    )
                if item.state not in DELIVERABLE_STATES:
                    raise LineageCorruptionError(
                        "release deliverable state is invalid"
                    )
                if item.required and item.state == "NOT_REQUIRED":
                    raise LineageCorruptionError(
                        "required release deliverable became NOT_REQUIRED"
                    )

            for row in self._conn.execute(
                "SELECT seq,id,plan_id,artist_id,song_id,label,lead_days,target_on,due_on "
                "FROM release_milestones ORDER BY seq"
            ):
                item = self._milestone(row)
                plan = self.get_plan(item.plan_id)
                if (
                    plan is None
                    or item.artist_id != plan.artist_id
                    or item.song_id != plan.song_id
                ):
                    raise LineageCorruptionError(
                        "release milestone binding is invalid"
                    )
                if item.target_on != plan.target_on:
                    raise LineageCorruptionError(
                        "release milestone target date does not match plan"
                    )
                days = _whole_days(item.lead_days)
                expected_due = (
                    date.fromisoformat(item.target_on) - timedelta(days=days)
                ).isoformat()
                if item.due_on != expected_due:
                    raise LineageCorruptionError(
                        "release milestone due date is not deterministic"
                    )
                if item.state not in MILESTONE_STATES:
                    raise LineageCorruptionError(
                        "release milestone state is invalid"
                    )
        except LineageCorruptionError:
            raise
        except (sqlite3.DatabaseError, ValueError, ValidationError, TypeError) as exc:
            raise LineageCorruptionError(
                "Release readiness memory is unreadable or corrupt"
            ) from exc

    def create_plan(self, song_id: str, *, target_on: str) -> ReleasePlan:
        song = self.store._require_song(
            _required_text(song_id, "Song id", maximum=200)
        )
        if song.artist_id != self.store.primary_artist_id:
            raise ValidationError(
                "release plan Song belongs to a different Artist"
            )
        target = _iso_date(target_on, "target_on")
        plan_id = f"release_{uuid.uuid4().hex}"
        try:
            with self.store._tx():
                self._conn.execute(
                    "INSERT INTO release_plans("
                    "id,artist_id,song_id,target_on,state,archived_note) "
                    "VALUES(?,?,?,?, 'ACTIVE', NULL)",
                    (plan_id, self.store.primary_artist_id, song.id, target),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(
                "this Song already has an active release plan"
            ) from exc
        plan = self.get_plan(plan_id)
        if plan is None:
            raise LineageCorruptionError(
                "new release plan disappeared after creation"
            )
        return plan

    def get_plan(self, plan_id: str) -> ReleasePlan | None:
        key = _required_text(plan_id, "release plan id", maximum=200)
        row = self._conn.execute(
            "SELECT seq,id,artist_id,song_id,target_on,state,archived_note "
            "FROM release_plans WHERE id=?",
            (key,),
        ).fetchone()
        return None if row is None else self._plan(row)

    def active_plan_for_song(self, song_id: str) -> ReleasePlan | None:
        key = _required_text(song_id, "Song id", maximum=200)
        row = self._conn.execute(
            "SELECT seq,id,artist_id,song_id,target_on,state,archived_note "
            "FROM release_plans WHERE song_id=? AND state='ACTIVE' "
            "ORDER BY seq DESC LIMIT 1",
            (key,),
        ).fetchone()
        return None if row is None else self._plan(row)

    def plan_history(self, song_id: str) -> tuple[ReleasePlan, ...]:
        key = _required_text(song_id, "Song id", maximum=200)
        return tuple(
            self._plan(row)
            for row in self._conn.execute(
                "SELECT seq,id,artist_id,song_id,target_on,state,archived_note "
                "FROM release_plans WHERE song_id=? ORDER BY seq",
                (key,),
            )
        )

    def plan_binding(self, plan_id: str) -> PlanBinding:
        plan = self.get_plan(plan_id)
        if plan is None:
            raise NotFoundError("release plan not found")
        return PlanBinding(
            plan_id=plan.id,
            song_id=plan.song_id,
            expected_state=plan.state,
            expected_revision=self._plan_revision(plan.id),
        )

    def archive_plan(self, binding: PlanBinding, *, note: str) -> ReleasePlan:
        plan = self._require_active_binding(binding)
        reason = _required_text(note, "archive note", maximum=1000)
        try:
            with self.store._tx():
                if self._plan_revision(plan.id) != binding.expected_revision:
                    raise StaleReleasePlanError(
                        "release plan changed before it could archive"
                    )
                changed = self._conn.execute(
                    "UPDATE release_plans SET state='ARCHIVED', archived_note=? "
                    "WHERE id=? AND state='ACTIVE'",
                    (reason, plan.id),
                )
                if changed.rowcount != 1:
                    raise StaleReleasePlanError(
                        "release plan changed before it could archive"
                    )
        except sqlite3.IntegrityError as exc:
            raise ReleaseReadinessError(
                "release plan could not archive safely"
            ) from exc
        archived = self.get_plan(plan.id)
        if archived is None or archived.state != "ARCHIVED":
            raise LineageCorruptionError("archived release plan disappeared")
        return archived

    def add_deliverable(
        self,
        binding: PlanBinding,
        *,
        kind: str,
        label: str,
        required: bool = True,
        state: str = "UNKNOWN",
        note: str | None = None,
    ) -> ReleaseDeliverable:
        if type(required) is not bool:
            raise ValidationError("required must be true or false")
        plan = self._require_active_binding(binding)
        item_kind = _enum_text(kind, "deliverable kind", DELIVERABLE_KINDS)
        item_label = _required_text(
            label,
            "deliverable label",
            maximum=240,
        )
        item_state = _enum_text(
            state,
            "deliverable state",
            DELIVERABLE_STATES,
        )
        if required and item_state == "NOT_REQUIRED":
            raise ValidationError(
                "a required release deliverable cannot be NOT_REQUIRED"
            )
        item_note = _optional_text(
            note,
            "deliverable note",
            maximum=1200,
        )
        item_id = f"reldel_{uuid.uuid4().hex}"
        event_id = f"reldevt_{uuid.uuid4().hex}"
        try:
            with self.store._tx():
                if self._plan_revision(plan.id) != binding.expected_revision:
                    raise StaleReleasePlanError(
                        "release plan changed before the deliverable committed"
                    )
                self._conn.execute(
                    "INSERT INTO release_deliverables("
                    "id,plan_id,artist_id,song_id,kind,label,required) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        item_id,
                        plan.id,
                        plan.artist_id,
                        plan.song_id,
                        item_kind,
                        item_label,
                        int(required),
                    ),
                )
                self._conn.execute(
                    "INSERT INTO release_deliverable_events("
                    "id,deliverable_id,state,note) VALUES(?,?,?,?)",
                    (event_id, item_id, item_state, item_note),
                )
        except sqlite3.IntegrityError as exc:
            raise ReleaseReadinessError(
                "release deliverable could not be recorded safely"
            ) from exc
        item = self.get_deliverable(item_id)
        if item is None:
            raise LineageCorruptionError(
                "new release deliverable disappeared"
            )
        return item

    def get_deliverable(
        self,
        deliverable_id: str,
    ) -> ReleaseDeliverable | None:
        key = _required_text(
            deliverable_id,
            "release deliverable id",
            maximum=200,
        )
        row = self._conn.execute(
            "SELECT seq,id,plan_id,artist_id,song_id,kind,label,required "
            "FROM release_deliverables WHERE id=?",
            (key,),
        ).fetchone()
        return None if row is None else self._deliverable(row)

    def deliverables_for_plan(
        self,
        plan_id: str,
    ) -> tuple[ReleaseDeliverable, ...]:
        key = _required_text(plan_id, "release plan id", maximum=200)
        return tuple(
            self._deliverable(row)
            for row in self._conn.execute(
                "SELECT seq,id,plan_id,artist_id,song_id,kind,label,required "
                "FROM release_deliverables WHERE plan_id=? ORDER BY seq",
                (key,),
            )
        )

    def deliverable_binding(
        self,
        deliverable_id: str,
    ) -> DeliverableBinding:
        item = self.get_deliverable(deliverable_id)
        if item is None:
            raise NotFoundError("release deliverable not found")
        plan = self.get_plan(item.plan_id)
        if plan is None:
            raise LineageCorruptionError(
                "release deliverable plan disappeared"
            )
        return DeliverableBinding(
            deliverable_id=item.id,
            plan_id=plan.id,
            expected_plan_revision=self._plan_revision(plan.id),
            expected_state_sequence=item.state_sequence,
            expected_state=item.state,
        )

    def set_deliverable_state(
        self,
        binding: DeliverableBinding,
        *,
        state: str,
        note: str | None = None,
    ) -> ReleaseDeliverable:
        if not isinstance(binding, DeliverableBinding):
            raise TypeError("binding must be DeliverableBinding")
        target = _enum_text(
            state,
            "deliverable state",
            DELIVERABLE_STATES,
        )
        item_note = _optional_text(
            note,
            "deliverable note",
            maximum=1200,
        )
        current = self.get_deliverable(binding.deliverable_id)
        plan = self.get_plan(binding.plan_id)
        if current is None or plan is None or current.plan_id != plan.id:
            raise StaleReleasePlanError(
                "release deliverable binding is no longer valid"
            )
        if plan.state != "ACTIVE":
            raise StaleReleasePlanError(
                "release plan changed after this deliverable action was prepared"
            )
        if self._plan_revision(plan.id) != binding.expected_plan_revision:
            raise StaleReleasePlanError(
                "release plan changed after this deliverable action was prepared"
            )
        if (
            current.state_sequence != binding.expected_state_sequence
            or current.state != binding.expected_state
        ):
            raise StaleReleasePlanError(
                "release deliverable state changed after this action was prepared"
            )
        if current.required and target == "NOT_REQUIRED":
            raise ValidationError(
                "a required release deliverable cannot be NOT_REQUIRED"
            )
        if target == current.state:
            raise StaleReleasePlanError(
                "release deliverable is already in that state"
            )
        event_id = f"reldevt_{uuid.uuid4().hex}"
        try:
            with self.store._tx():
                latest = self._conn.execute(
                    "SELECT seq,state FROM release_deliverable_events "
                    "WHERE deliverable_id=? ORDER BY seq DESC LIMIT 1",
                    (current.id,),
                ).fetchone()
                if (
                    latest is None
                    or int(latest["seq"]) != binding.expected_state_sequence
                    or str(latest["state"]) != binding.expected_state
                    or self._plan_revision(plan.id)
                    != binding.expected_plan_revision
                ):
                    raise StaleReleasePlanError(
                        "release deliverable or plan changed before commit"
                    )
                self._conn.execute(
                    "INSERT INTO release_deliverable_events("
                    "id,deliverable_id,state,note) VALUES(?,?,?,?)",
                    (event_id, current.id, target, item_note),
                )
        except sqlite3.IntegrityError as exc:
            raise ReleaseReadinessError(
                "release deliverable state could not be recorded safely"
            ) from exc
        updated = self.get_deliverable(current.id)
        if updated is None or updated.state != target:
            raise LineageCorruptionError(
                "release deliverable state disappeared after commit"
            )
        return updated

    def add_milestone(
        self,
        binding: PlanBinding,
        *,
        label: str,
        lead_days: int,
        state: str = "OPEN",
        note: str | None = None,
    ) -> ReleaseMilestone:
        plan = self._require_active_binding(binding)
        item_label = _required_text(
            label,
            "milestone label",
            maximum=240,
        )
        days = _whole_days(lead_days)
        item_state = _enum_text(
            state,
            "milestone state",
            MILESTONE_STATES,
        )
        item_note = _optional_text(
            note,
            "milestone note",
            maximum=1200,
        )
        due_on = (
            date.fromisoformat(plan.target_on) - timedelta(days=days)
        ).isoformat()
        item_id = f"relmile_{uuid.uuid4().hex}"
        event_id = f"relmevt_{uuid.uuid4().hex}"
        try:
            with self.store._tx():
                if self._plan_revision(plan.id) != binding.expected_revision:
                    raise StaleReleasePlanError(
                        "release plan changed before the milestone committed"
                    )
                self._conn.execute(
                    "INSERT INTO release_milestones("
                    "id,plan_id,artist_id,song_id,label,lead_days,target_on,due_on) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        item_id,
                        plan.id,
                        plan.artist_id,
                        plan.song_id,
                        item_label,
                        days,
                        plan.target_on,
                        due_on,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO release_milestone_events("
                    "id,milestone_id,state,note) VALUES(?,?,?,?)",
                    (event_id, item_id, item_state, item_note),
                )
        except sqlite3.IntegrityError as exc:
            raise ReleaseReadinessError(
                "release milestone could not be recorded safely"
            ) from exc
        item = self.get_milestone(item_id)
        if item is None:
            raise LineageCorruptionError("new release milestone disappeared")
        return item

    def get_milestone(self, milestone_id: str) -> ReleaseMilestone | None:
        key = _required_text(
            milestone_id,
            "release milestone id",
            maximum=200,
        )
        row = self._conn.execute(
            "SELECT seq,id,plan_id,artist_id,song_id,label,lead_days,target_on,due_on "
            "FROM release_milestones WHERE id=?",
            (key,),
        ).fetchone()
        return None if row is None else self._milestone(row)

    def milestones_for_plan(
        self,
        plan_id: str,
    ) -> tuple[ReleaseMilestone, ...]:
        key = _required_text(plan_id, "release plan id", maximum=200)
        return tuple(
            self._milestone(row)
            for row in self._conn.execute(
                "SELECT seq,id,plan_id,artist_id,song_id,label,lead_days,target_on,due_on "
                "FROM release_milestones WHERE plan_id=? ORDER BY due_on,seq",
                (key,),
            )
        )

    def milestone_binding(self, milestone_id: str) -> MilestoneBinding:
        item = self.get_milestone(milestone_id)
        if item is None:
            raise NotFoundError("release milestone not found")
        plan = self.get_plan(item.plan_id)
        if plan is None:
            raise LineageCorruptionError(
                "release milestone plan disappeared"
            )
        return MilestoneBinding(
            milestone_id=item.id,
            plan_id=plan.id,
            expected_plan_revision=self._plan_revision(plan.id),
            expected_state_sequence=item.state_sequence,
            expected_state=item.state,
        )

    def set_milestone_state(
        self,
        binding: MilestoneBinding,
        *,
        state: str,
        note: str | None = None,
    ) -> ReleaseMilestone:
        if not isinstance(binding, MilestoneBinding):
            raise TypeError("binding must be MilestoneBinding")
        target = _enum_text(
            state,
            "milestone state",
            MILESTONE_STATES,
        )
        item_note = _optional_text(
            note,
            "milestone note",
            maximum=1200,
        )
        current = self.get_milestone(binding.milestone_id)
        plan = self.get_plan(binding.plan_id)
        if current is None or plan is None or current.plan_id != plan.id:
            raise StaleReleasePlanError(
                "release milestone binding is no longer valid"
            )
        if plan.state != "ACTIVE":
            raise StaleReleasePlanError(
                "release plan changed after this milestone action was prepared"
            )
        if self._plan_revision(plan.id) != binding.expected_plan_revision:
            raise StaleReleasePlanError(
                "release plan changed after this milestone action was prepared"
            )
        if (
            current.state_sequence != binding.expected_state_sequence
            or current.state != binding.expected_state
        ):
            raise StaleReleasePlanError(
                "release milestone state changed after this action was prepared"
            )
        if target == current.state:
            raise StaleReleasePlanError(
                "release milestone is already in that state"
            )
        event_id = f"relmevt_{uuid.uuid4().hex}"
        try:
            with self.store._tx():
                latest = self._conn.execute(
                    "SELECT seq,state FROM release_milestone_events "
                    "WHERE milestone_id=? ORDER BY seq DESC LIMIT 1",
                    (current.id,),
                ).fetchone()
                if (
                    latest is None
                    or int(latest["seq"]) != binding.expected_state_sequence
                    or str(latest["state"]) != binding.expected_state
                    or self._plan_revision(plan.id)
                    != binding.expected_plan_revision
                ):
                    raise StaleReleasePlanError(
                        "release milestone or plan changed before commit"
                    )
                self._conn.execute(
                    "INSERT INTO release_milestone_events("
                    "id,milestone_id,state,note) VALUES(?,?,?,?)",
                    (event_id, current.id, target, item_note),
                )
        except sqlite3.IntegrityError as exc:
            raise ReleaseReadinessError(
                "release milestone state could not be recorded safely"
            ) from exc
        updated = self.get_milestone(current.id)
        if updated is None or updated.state != target:
            raise LineageCorruptionError(
                "release milestone state disappeared after commit"
            )
        return updated

    def snapshot(self, plan_id: str) -> ReleaseReadinessSnapshot:
        plan = self.get_plan(plan_id)
        if plan is None:
            raise NotFoundError("release plan not found")
        if plan.state != "ACTIVE":
            raise StaleReleasePlanError(
                "archived release plans have history, not a current readiness snapshot"
            )
        song = self.store.get_song(plan.song_id)
        if song is None or song.artist_id != plan.artist_id:
            raise LineageCorruptionError("release plan Song disappeared")
        deliverables = self.deliverables_for_plan(plan.id)
        milestones = self.milestones_for_plan(plan.id)
        approved_state = (
            "PRESENT" if song.approved_version_id is not None else "MISSING"
        )
        if approved_state not in APPROVED_VERSION_STATES:
            raise LineageCorruptionError(
                "approved Version prerequisite state is invalid"
            )

        unresolved: list[str] = []
        required_items = tuple(item for item in deliverables if item.required)
        required_states = [item.state for item in required_items]

        if approved_state == "MISSING":
            unresolved.append(
                "Approve the exact Song Version intended for release; approval is not delivery."
            )
        if not required_items:
            unresolved.append(
                "No required release deliverables are defined yet; define what this exact release needs before treating the plan as review-ready."
            )
        for item in required_items:
            if item.state in {"UNKNOWN", "MISSING", "BLOCKED"}:
                unresolved.append(f"{item.label}: {item.state}")
        for item in milestones:
            if item.state in {"OPEN", "BLOCKED"}:
                unresolved.append(
                    f"{item.label}: {item.state} (due {item.due_on})"
                )

        if "BLOCKED" in required_states or any(
            item.state == "BLOCKED" for item in milestones
        ):
            review_state = "BLOCKED"
        elif approved_state == "MISSING" or "MISSING" in required_states:
            review_state = "MISSING"
        elif not required_items or "UNKNOWN" in required_states:
            review_state = "UNKNOWN"
        elif any(item.state == "OPEN" for item in milestones):
            review_state = "IN_PROGRESS"
        else:
            review_state = "READY_FOR_REVIEW"

        if review_state not in REVIEW_STATES:
            raise LineageCorruptionError("release review state is invalid")
        return ReleaseReadinessSnapshot(
            plan=plan,
            approved_version_id=song.approved_version_id,
            approved_version_state=approved_state,
            deliverables=deliverables,
            milestones=milestones,
            review_state=review_state,
            unresolved=tuple(unresolved),
        )
