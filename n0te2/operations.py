from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from .activity import ActivityLog
from .authority import ActionIntent, ApprovalBinding, AuthorityService
from .lineage import (
    LineageCorruptionError,
    LineageStore,
    NotFoundError,
    ValidationError,
)
from .network import TransportDecision

OPERATION_SCHEMA_VERSION = 1
OPERATION_EVENTS = {
    "PREPARED",
    "EXECUTION_CLAIMED",
    "SUCCEEDED",
    "FAILED",
    "UNKNOWN",
    "RECONCILED_SUCCEEDED",
    "RECONCILED_FAILED",
}
RECORDED_STATES = {"PREPARED", "EXECUTING", "SUCCEEDED", "FAILED", "UNKNOWN"}
EFFECTIVE_OUTCOMES = {"SUCCEEDED", "FAILED", "UNKNOWN"}


class OperationError(RuntimeError):
    """Operation lifecycle or execute-once contract violation."""


class DuplicateExecutionError(OperationError):
    """An operation has already been claimed or reached an outcome."""


def _text(value: str, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValidationError(f"{field} must not be empty")
    return text


def _optional_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class OperationEvent:
    sequence: int
    id: str
    operation_id: str
    event_type: str
    evidence_ref: str | None
    receipt_ref: str | None
    result_fingerprint: str | None


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    idempotency_key: str
    intent_fingerprint: str
    approval_id: str
    approval_source_ref: str
    song_id: str | None
    version_id: str | None
    recorded_state: str
    effective_outcome: str | None
    attempt_count: int
    receipt_ref: str | None
    evidence_ref: str | None
    result_fingerprint: str | None
    reconciled: bool
    reconciliation_evidence_ref: str | None


class OperationJournal:
    """Durable execute-once identity and truthful outcome history.

    The journal owns no provider, DAW or network implementation. `claim_execution`
    atomically grants one execution claim for an exact approved intent. Outbound
    intents additionally require an explicit CORE-04C transport ALLOW decision.
    The caller may then execute through a separately authorized adapter and must
    record a truthful outcome. UNKNOWN is terminal for retry purposes and can be
    resolved only by explicit evidence-backed reconciliation.
    """

    _TRIGGER_NAMES = {
        "operation_version_matches_song",
        "operations_immutable_update",
        "operations_immutable_delete",
        "operation_events_immutable_update",
        "operation_events_immutable_delete",
        "activity_operation_event",
    }

    def __init__(self, store: LineageStore, activity: ActivityLog):
        if not isinstance(store, LineageStore):
            raise TypeError("OperationJournal requires the canonical LineageStore")
        if not isinstance(activity, ActivityLog) or activity.store is not store:
            raise TypeError("OperationJournal requires ActivityLog for the same store")
        self.store = store
        self.activity = activity
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

    @staticmethod
    def _trigger_statements() -> tuple[str, ...]:
        return (
            """CREATE TRIGGER operation_version_matches_song
            BEFORE INSERT ON operations
            WHEN NEW.version_id IS NOT NULL AND (
                NEW.song_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM versions v
                    WHERE v.id=NEW.version_id AND v.song_id=NEW.song_id
                )
            )
            BEGIN
                SELECT RAISE(ABORT, 'operation version belongs to a different Song');
            END""",
            """CREATE TRIGGER operations_immutable_update
            BEFORE UPDATE ON operations
            BEGIN
                SELECT RAISE(ABORT, 'operation identity is immutable');
            END""",
            """CREATE TRIGGER operations_immutable_delete
            BEFORE DELETE ON operations
            BEGIN
                SELECT RAISE(ABORT, 'operation identity is immutable');
            END""",
            """CREATE TRIGGER operation_events_immutable_update
            BEFORE UPDATE ON operation_events
            BEGIN
                SELECT RAISE(ABORT, 'operation history is append-only');
            END""",
            """CREATE TRIGGER operation_events_immutable_delete
            BEFORE DELETE ON operation_events
            BEGIN
                SELECT RAISE(ABORT, 'operation history is append-only');
            END""",
            """CREATE TRIGGER activity_operation_event
            AFTER INSERT ON operation_events
            BEGIN
                INSERT INTO activity_events(
                    id,event_type,artist_id,song_id,version_id,object_type,object_id,payload_json
                )
                SELECT
                    'act_'||lower(hex(randomblob(16))),
                    'OPERATION_'||NEW.event_type,
                    (SELECT value FROM metadata WHERE key='primary_artist_id'),
                    o.song_id,
                    o.version_id,
                    'OPERATION',
                    o.id,
                    '{}'
                FROM operations o WHERE o.id=NEW.operation_id;
            END""",
        )

    def _ensure_schema(self) -> None:
        operations_exists = self._table_exists("operations")
        events_exists = self._table_exists("operation_events")
        version = self._metadata_value("operation_schema_version")
        if operations_exists != events_exists or operations_exists != (version is not None):
            raise LineageCorruptionError("operation schema metadata/table mismatch")
        if operations_exists:
            if version != str(OPERATION_SCHEMA_VERSION):
                raise LineageCorruptionError(
                    f"unsupported operation schema version: {version}"
                )
            return
        if not self._table_exists("activity_events"):
            raise LineageCorruptionError(
                "OperationJournal requires ActivityLog to initialize first"
            )
        try:
            with self.store._tx():
                self._conn.execute(
                    """CREATE TABLE operations (
                        id TEXT PRIMARY KEY,
                        idempotency_key TEXT NOT NULL UNIQUE CHECK(length(trim(idempotency_key)) > 0),
                        intent_fingerprint TEXT NOT NULL CHECK(length(trim(intent_fingerprint)) > 0),
                        approval_id TEXT NOT NULL CHECK(length(trim(approval_id)) > 0),
                        approval_source_ref TEXT NOT NULL CHECK(length(trim(approval_source_ref)) > 0),
                        song_id TEXT NULL REFERENCES songs(id),
                        version_id TEXT NULL REFERENCES versions(id)
                    )"""
                )
                self._conn.execute(
                    """CREATE TABLE operation_events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        operation_id TEXT NOT NULL REFERENCES operations(id),
                        event_type TEXT NOT NULL CHECK(event_type IN (
                            'PREPARED','EXECUTION_CLAIMED','SUCCEEDED','FAILED','UNKNOWN',
                            'RECONCILED_SUCCEEDED','RECONCILED_FAILED'
                        )),
                        evidence_ref TEXT NULL,
                        receipt_ref TEXT NULL,
                        result_fingerprint TEXT NULL
                    )"""
                )
                self._conn.execute(
                    "CREATE INDEX operation_events_by_operation ON operation_events(operation_id,seq)"
                )
                for statement in self._trigger_statements():
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT INTO metadata(key,value) VALUES('operation_schema_version',?)",
                    (str(OPERATION_SCHEMA_VERSION),),
                )
        except sqlite3.DatabaseError as exc:
            raise LineageCorruptionError("cannot initialize Operation Journal") from exc

    @staticmethod
    def _event(row: sqlite3.Row) -> OperationEvent:
        return OperationEvent(
            sequence=int(row["seq"]),
            id=str(row["id"]),
            operation_id=str(row["operation_id"]),
            event_type=str(row["event_type"]),
            evidence_ref=None if row["evidence_ref"] is None else str(row["evidence_ref"]),
            receipt_ref=None if row["receipt_ref"] is None else str(row["receipt_ref"]),
            result_fingerprint=(
                None if row["result_fingerprint"] is None else str(row["result_fingerprint"])
            ),
        )

    def _events(self, operation_id: str) -> tuple[OperationEvent, ...]:
        return tuple(
            self._event(row)
            for row in self._conn.execute(
                "SELECT seq,id,operation_id,event_type,evidence_ref,receipt_ref,result_fingerprint "
                "FROM operation_events WHERE operation_id=? ORDER BY seq",
                (operation_id,),
            )
        )

    @staticmethod
    def _derive(events: tuple[OperationEvent, ...]) -> tuple[str, str | None, int, bool]:
        if not events or events[0].event_type != "PREPARED":
            raise LineageCorruptionError("operation history must begin with PREPARED")
        state = "PREPARED"
        effective: str | None = None
        attempts = 0
        reconciled = False
        for index, event in enumerate(events[1:], start=1):
            kind = event.event_type
            if kind == "EXECUTION_CLAIMED":
                if state != "PREPARED" or attempts != 0:
                    raise LineageCorruptionError("operation execution claim sequence is invalid")
                state = "EXECUTING"
                attempts = 1
            elif kind == "SUCCEEDED":
                if state != "EXECUTING" or effective is not None:
                    raise LineageCorruptionError("operation success sequence is invalid")
                state = "SUCCEEDED"
                effective = "SUCCEEDED"
            elif kind == "FAILED":
                if state != "EXECUTING" or effective is not None:
                    raise LineageCorruptionError("operation failure sequence is invalid")
                state = "FAILED"
                effective = "FAILED"
            elif kind == "UNKNOWN":
                if state != "EXECUTING" or effective is not None:
                    raise LineageCorruptionError("operation unknown sequence is invalid")
                state = "UNKNOWN"
                effective = "UNKNOWN"
            elif kind in {"RECONCILED_SUCCEEDED", "RECONCILED_FAILED"}:
                if state != "UNKNOWN" or effective != "UNKNOWN" or reconciled:
                    raise LineageCorruptionError("operation reconciliation sequence is invalid")
                effective = "SUCCEEDED" if kind == "RECONCILED_SUCCEEDED" else "FAILED"
                reconciled = True
            else:
                raise LineageCorruptionError(f"unknown operation event: {kind}")
            if reconciled and index != len(events) - 1:
                raise LineageCorruptionError("operation history continues after reconciliation")
            if state in {"SUCCEEDED", "FAILED"} and index != len(events) - 1:
                raise LineageCorruptionError("operation history continues after known terminal outcome")
        return state, effective, attempts, reconciled

    @staticmethod
    def _validate_event_evidence(events: tuple[OperationEvent, ...]) -> None:
        for event in events:
            if event.event_type in {"PREPARED", "EXECUTION_CLAIMED"}:
                if not event.evidence_ref:
                    raise LineageCorruptionError(
                        f"{event.event_type} operation event requires evidence"
                    )
            elif event.event_type == "SUCCEEDED":
                if not event.receipt_ref or not event.evidence_ref or not event.result_fingerprint:
                    raise LineageCorruptionError(
                        "successful operation requires receipt, evidence and result fingerprint"
                    )
            elif event.event_type in {"FAILED", "UNKNOWN"}:
                if not event.evidence_ref:
                    raise LineageCorruptionError(
                        f"{event.event_type} operation requires evidence"
                    )
            elif event.event_type == "RECONCILED_SUCCEEDED":
                if not event.receipt_ref or not event.evidence_ref or not event.result_fingerprint:
                    raise LineageCorruptionError(
                        "reconciled success requires receipt, evidence and result fingerprint"
                    )
            elif event.event_type == "RECONCILED_FAILED":
                if not event.evidence_ref:
                    raise LineageCorruptionError(
                        "reconciled failure requires evidence"
                    )

    def _validate_existing(self) -> None:
        try:
            trigger_names = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND "
                    "(name LIKE 'operation_%' OR name='activity_operation_event')"
                )
            }
            missing = self._TRIGGER_NAMES - trigger_names
            if missing:
                raise LineageCorruptionError(
                    f"operation hooks are incomplete: {sorted(missing)}"
                )
            invalid = self._conn.execute(
                "SELECT o.id FROM operations o "
                "LEFT JOIN songs s ON s.id=o.song_id "
                "LEFT JOIN versions v ON v.id=o.version_id "
                "WHERE (o.song_id IS NOT NULL AND s.id IS NULL) "
                "OR (o.version_id IS NOT NULL AND (v.id IS NULL OR o.song_id IS NULL OR v.song_id<>o.song_id)) "
                "LIMIT 1"
            ).fetchone()
            if invalid is not None:
                raise LineageCorruptionError(
                    "operation history contains invalid Song/version references"
                )
            for row in self._conn.execute("SELECT id FROM operations ORDER BY id"):
                events = self._events(str(row["id"]))
                self._derive(events)
                self._validate_event_evidence(events)
        except LineageCorruptionError:
            raise
        except Exception as exc:
            raise LineageCorruptionError("operation history is unreadable or corrupt") from exc

    def _identity_row(self, operation_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT id,idempotency_key,intent_fingerprint,approval_id,approval_source_ref,song_id,version_id "
            "FROM operations WHERE id=?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"operation not found: {operation_id}")
        return row

    def _record(self, row: sqlite3.Row) -> OperationRecord:
        operation_id = str(row["id"])
        events = self._events(operation_id)
        state, effective, attempts, reconciled = self._derive(events)
        terminal = events[-1]
        reconciliation = terminal if terminal.event_type.startswith("RECONCILED_") else None
        return OperationRecord(
            operation_id=operation_id,
            idempotency_key=str(row["idempotency_key"]),
            intent_fingerprint=str(row["intent_fingerprint"]),
            approval_id=str(row["approval_id"]),
            approval_source_ref=str(row["approval_source_ref"]),
            song_id=None if row["song_id"] is None else str(row["song_id"]),
            version_id=None if row["version_id"] is None else str(row["version_id"]),
            recorded_state=state,
            effective_outcome=effective,
            attempt_count=attempts,
            receipt_ref=terminal.receipt_ref,
            evidence_ref=terminal.evidence_ref,
            result_fingerprint=terminal.result_fingerprint,
            reconciled=reconciled,
            reconciliation_evidence_ref=(
                None if reconciliation is None else reconciliation.evidence_ref
            ),
        )

    def get(self, operation_id: str) -> OperationRecord:
        return self._record(self._identity_row(operation_id))

    def by_idempotency_key(self, idempotency_key: str) -> OperationRecord | None:
        key = _text(idempotency_key, "idempotency_key")
        row = self._conn.execute(
            "SELECT id,idempotency_key,intent_fingerprint,approval_id,approval_source_ref,song_id,version_id "
            "FROM operations WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        return None if row is None else self._record(row)

    def events(self, operation_id: str) -> tuple[OperationEvent, ...]:
        self._identity_row(operation_id)
        return self._events(operation_id)

    def _validate_song_version(
        self, song_id: str | None, version_id: str | None
    ) -> tuple[str | None, str | None]:
        if song_id is None and version_id is not None:
            raise ValidationError("version_id requires song_id")
        if song_id is None:
            return None, None
        song = self.store.get_song(song_id)
        if song is None:
            raise NotFoundError(
                f"Song not found in profile {self.store.profile_id}: {song_id}"
            )
        if version_id is None:
            return song.id, None
        version = self.store.get_version(version_id)
        if version is None:
            raise NotFoundError(f"version not found: {version_id}")
        if version.song_id != song.id:
            raise ValidationError("version belongs to a different Song")
        return song.id, version.id

    @staticmethod
    def _require_valid_approval(intent: ActionIntent, approval: ApprovalBinding) -> None:
        validation = AuthorityService.validate(intent, approval)
        if validation.status != "VALID":
            raise OperationError("approval is stale for the current action intent")

    @staticmethod
    def _require_transport_gate(
        intent: ActionIntent,
        transport_decision: TransportDecision | None,
    ) -> None:
        if intent.destination is None:
            if transport_decision is not None and transport_decision.status != "ALLOW":
                raise OperationError("supplied transport decision denies execution")
            return
        if transport_decision is None:
            raise OperationError("outbound operation requires an explicit transport decision")
        if not isinstance(transport_decision, TransportDecision):
            raise TypeError("transport_decision must be TransportDecision")
        if transport_decision.status != "ALLOW":
            raise OperationError("transport policy denies outbound execution")
        if transport_decision.action_authority_granted is not False:
            raise OperationError("transport decision must not grant action authority")

    def _append_event(
        self,
        operation_id: str,
        event_type: str,
        *,
        evidence_ref: str | None = None,
        receipt_ref: str | None = None,
        result_fingerprint: str | None = None,
    ) -> None:
        if event_type not in OPERATION_EVENTS:
            raise OperationError(f"unsupported operation event: {event_type}")
        self._conn.execute(
            "INSERT INTO operation_events(id,operation_id,event_type,evidence_ref,receipt_ref,result_fingerprint) "
            "VALUES(?,?,?,?,?,?)",
            (
                _new_id("opevt"),
                operation_id,
                event_type,
                evidence_ref,
                receipt_ref,
                result_fingerprint,
            ),
        )

    def prepare(
        self,
        *,
        idempotency_key: str,
        intent: ActionIntent,
        approval: ApprovalBinding,
        song_id: str | None = None,
        version_id: str | None = None,
    ) -> OperationRecord:
        if not isinstance(intent, ActionIntent):
            raise TypeError("intent must be ActionIntent")
        if not isinstance(approval, ApprovalBinding):
            raise TypeError("approval must be ApprovalBinding")
        key = _text(idempotency_key, "idempotency_key")
        self._require_valid_approval(intent, approval)
        song_id, version_id = self._validate_song_version(song_id, version_id)

        existing = self.by_idempotency_key(key)
        if existing is not None:
            if (
                existing.intent_fingerprint == intent.intent_fingerprint
                and existing.approval_id == approval.approval_id
                and existing.approval_source_ref == approval.source_ref
                and existing.song_id == song_id
                and existing.version_id == version_id
            ):
                return existing
            raise OperationError("idempotency key is already bound to a different operation")

        operation_id = _new_id("op")
        try:
            with self.store._tx():
                self._conn.execute(
                    "INSERT INTO operations(id,idempotency_key,intent_fingerprint,approval_id,approval_source_ref,song_id,version_id) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        operation_id,
                        key,
                        intent.intent_fingerprint,
                        approval.approval_id,
                        approval.source_ref,
                        song_id,
                        version_id,
                    ),
                )
                self._append_event(
                    operation_id,
                    "PREPARED",
                    evidence_ref=approval.source_ref,
                )
        except sqlite3.IntegrityError as exc:
            existing = self.by_idempotency_key(key)
            if existing is not None and (
                existing.intent_fingerprint == intent.intent_fingerprint
                and existing.approval_id == approval.approval_id
                and existing.approval_source_ref == approval.source_ref
                and existing.song_id == song_id
                and existing.version_id == version_id
            ):
                return existing
            raise OperationError("cannot prepare operation with this idempotency key") from exc
        return self.get(operation_id)

    def _require_exact_identity(
        self,
        operation: OperationRecord,
        intent: ActionIntent,
        approval: ApprovalBinding,
    ) -> None:
        self._require_valid_approval(intent, approval)
        if operation.intent_fingerprint != intent.intent_fingerprint:
            raise OperationError("operation is bound to a different action intent")
        if operation.approval_id != approval.approval_id:
            raise OperationError("operation is bound to a different approval")
        if operation.approval_source_ref != approval.source_ref:
            raise OperationError("operation approval source does not match")

    def claim_execution(
        self,
        operation_id: str,
        *,
        intent: ActionIntent,
        approval: ApprovalBinding,
        claim_evidence_ref: str,
        transport_decision: TransportDecision | None = None,
    ) -> OperationRecord:
        claim_ref = _text(claim_evidence_ref, "claim_evidence_ref")
        with self.store._tx():
            operation = self.get(operation_id)
            self._require_exact_identity(operation, intent, approval)
            self._require_transport_gate(intent, transport_decision)
            if operation.recorded_state != "PREPARED":
                raise DuplicateExecutionError(
                    f"operation cannot be claimed from state {operation.recorded_state}"
                )
            self._append_event(
                operation_id,
                "EXECUTION_CLAIMED",
                evidence_ref=claim_ref,
            )
        return self.get(operation_id)

    def complete_success(
        self,
        operation_id: str,
        *,
        receipt_ref: str,
        evidence_ref: str,
        result_fingerprint: str,
    ) -> OperationRecord:
        receipt = _text(receipt_ref, "receipt_ref")
        evidence = _text(evidence_ref, "evidence_ref")
        result = _text(result_fingerprint, "result_fingerprint")
        with self.store._tx():
            operation = self.get(operation_id)
            if operation.recorded_state != "EXECUTING":
                raise OperationError(
                    f"success cannot be recorded from state {operation.recorded_state}"
                )
            self._append_event(
                operation_id,
                "SUCCEEDED",
                evidence_ref=evidence,
                receipt_ref=receipt,
                result_fingerprint=result,
            )
        return self.get(operation_id)

    def complete_failure(
        self,
        operation_id: str,
        *,
        evidence_ref: str,
        receipt_ref: str | None = None,
        result_fingerprint: str | None = None,
    ) -> OperationRecord:
        evidence = _text(evidence_ref, "evidence_ref")
        receipt = _optional_text(receipt_ref, "receipt_ref")
        result = _optional_text(result_fingerprint, "result_fingerprint")
        with self.store._tx():
            operation = self.get(operation_id)
            if operation.recorded_state != "EXECUTING":
                raise OperationError(
                    f"failure cannot be recorded from state {operation.recorded_state}"
                )
            self._append_event(
                operation_id,
                "FAILED",
                evidence_ref=evidence,
                receipt_ref=receipt,
                result_fingerprint=result,
            )
        return self.get(operation_id)

    def mark_unknown(
        self,
        operation_id: str,
        *,
        evidence_ref: str,
        receipt_ref: str | None = None,
    ) -> OperationRecord:
        evidence = _text(evidence_ref, "evidence_ref")
        receipt = _optional_text(receipt_ref, "receipt_ref")
        with self.store._tx():
            operation = self.get(operation_id)
            if operation.recorded_state != "EXECUTING":
                raise OperationError(
                    f"UNKNOWN cannot be recorded from state {operation.recorded_state}"
                )
            self._append_event(
                operation_id,
                "UNKNOWN",
                evidence_ref=evidence,
                receipt_ref=receipt,
            )
        return self.get(operation_id)

    def reconcile_unknown(
        self,
        operation_id: str,
        *,
        observed_outcome: str,
        evidence_ref: str,
        receipt_ref: str | None = None,
        result_fingerprint: str | None = None,
    ) -> OperationRecord:
        outcome = str(observed_outcome).strip().upper()
        if outcome not in {"SUCCEEDED", "FAILED"}:
            raise OperationError("observed_outcome must be SUCCEEDED or FAILED")
        evidence = _text(evidence_ref, "evidence_ref")
        receipt = _optional_text(receipt_ref, "receipt_ref")
        result = _optional_text(result_fingerprint, "result_fingerprint")
        if outcome == "SUCCEEDED" and (receipt is None or result is None):
            raise OperationError(
                "reconciled success requires receipt_ref and result_fingerprint"
            )
        with self.store._tx():
            operation = self.get(operation_id)
            if operation.recorded_state != "UNKNOWN" or operation.reconciled:
                raise OperationError(
                    "only unreconciled UNKNOWN operations may be reconciled"
                )
            self._append_event(
                operation_id,
                f"RECONCILED_{outcome}",
                evidence_ref=evidence,
                receipt_ref=receipt,
                result_fingerprint=result,
            )
        return self.get(operation_id)
