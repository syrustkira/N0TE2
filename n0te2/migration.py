
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .instance import InstanceLease, InstanceLeaseManager, ProcessIdentity, ProcessProbe
from .lineage import LineageStore
from .memory import HeadquartersMemory
from .recovery import RecoveryManager, SnapshotInfo

APP_SCHEMA_KEY = "application_semantic_schema_version"
APP_SCHEMA_DEFAULT_VERSION = 1
MIGRATION_SCHEMA_VERSION = 1

_PROFILE_ID = re.compile(r"^prf_[0-9a-f]{32}$")
_MIGRATION_ID = re.compile(r"^mig_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_SQL = (
    "DROP ", "DELETE ", "TRUNCATE ", "ATTACH ", "DETACH ",
    "VACUUM", "WRITABLE_SCHEMA", "CREATE TRIGGER", "PRAGMA ",
)
_STATES = {
    "PREPARED", "STAGING", "INSTALLING", "VALIDATING", "RESTORING",
    "NO_CHANGE", "SUCCEEDED", "FAILED_SAFE", "ROLLED_BACK", "RECOVERY_REQUIRED",
}
_TERMINAL = {"NO_CHANGE", "SUCCEEDED", "FAILED_SAFE", "ROLLED_BACK", "RECOVERY_REQUIRED"}
_IN_FLIGHT = {"STAGING", "INSTALLING", "VALIDATING", "RESTORING"}
_TRANSITIONS = {
    "PREPARED": {"STAGING", "NO_CHANGE", "RECOVERY_REQUIRED"},
    "STAGING": {"INSTALLING", "FAILED_SAFE", "RECOVERY_REQUIRED"},
    "INSTALLING": {"VALIDATING", "FAILED_SAFE", "RECOVERY_REQUIRED"},
    "VALIDATING": {"SUCCEEDED", "RESTORING", "ROLLED_BACK", "RECOVERY_REQUIRED"},
    "RESTORING": {"ROLLED_BACK", "RECOVERY_REQUIRED"},
    "NO_CHANGE": set(), "SUCCEEDED": set(), "FAILED_SAFE": set(),
    "ROLLED_BACK": set(), "RECOVERY_REQUIRED": set(),
}


class SchemaMigrationError(RuntimeError):
    pass


class MigrationPlanError(SchemaMigrationError):
    pass


class MigrationValidationError(SchemaMigrationError):
    pass


class MigrationBusyError(SchemaMigrationError):
    pass


class MigrationExecuteOnceError(SchemaMigrationError):
    pass


class MigrationJournalCorruptionError(SchemaMigrationError):
    pass


class _DatabaseReleaseUncertain(BaseException):
    pass


def _text(value: str, field: str) -> str:
    value = " ".join(str(value).split())
    if not value:
        raise MigrationPlanError(f"{field} must not be empty")
    return value


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _sha(value: str, field: str) -> str:
    value = str(value).strip().lower()
    if not _SHA256.fullmatch(value):
        raise MigrationPlanError(f"{field} must be a lowercase SHA-256")
    return value


def _profile(value: str) -> str:
    value = str(value).strip()
    if not _PROFILE_ID.fullmatch(value):
        raise MigrationPlanError("invalid profile_id")
    return value


def _mig_id(value: str) -> str:
    value = str(value).strip().lower()
    if not _MIGRATION_ID.fullmatch(value):
        raise MigrationPlanError("invalid migration_id")
    return value


def _real_dir(path: str | Path, field: str, *, create: bool = False) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise MigrationPlanError(f"{field} must be absolute")
    lexical = Path(os.path.abspath(os.path.normpath(str(path))))
    if create:
        lexical.mkdir(parents=True, exist_ok=True)
    try:
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MigrationPlanError(f"{field} must exist as a real directory") from exc
    if resolved != lexical or not resolved.is_dir():
        raise MigrationPlanError(f"{field} must not traverse a symlink/filesystem alias")
    return resolved


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        os.close(fd)


def _qi(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _sql_value(value: object) -> object:
    if value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise MigrationValidationError("semantic state contains non-finite float")
        return {"float": repr(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"blob_hex": bytes(value).hex()}
    raise MigrationValidationError(f"unsupported SQLite value: {type(value).__name__}")


@dataclass(frozen=True)
class MigrationStep:
    from_version: int
    to_version: int
    description: str
    statements: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.from_version, bool) or not isinstance(self.from_version, int) or self.from_version < 1:
            raise MigrationPlanError("from_version must be a positive integer")
        if isinstance(self.to_version, bool) or not isinstance(self.to_version, int) or self.to_version != self.from_version + 1:
            raise MigrationPlanError("migration steps must advance exactly one version")
        object.__setattr__(self, "description", _text(self.description, "description"))
        statements = tuple(_text(x, "migration SQL") for x in self.statements)
        if not statements:
            raise MigrationPlanError("migration step requires SQL")
        for statement in statements:
            upper = " ".join(statement.upper().split())
            if any(token in upper for token in _FORBIDDEN_SQL):
                raise MigrationPlanError(
                    "destructive/attached/trigger/pragma migration SQL requires a separate consequential decision"
                )
        object.__setattr__(self, "statements", statements)

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_data())

    def to_data(self) -> dict[str, object]:
        return {
            "from_version": self.from_version,
            "to_version": self.to_version,
            "description": self.description,
            "statements": list(self.statements),
        }

    @classmethod
    def from_data(cls, data: object) -> "MigrationStep":
        if not isinstance(data, dict) or set(data) != {"from_version", "to_version", "description", "statements"}:
            raise MigrationJournalCorruptionError("migration step shape is invalid")
        try:
            return cls(
                data["from_version"], data["to_version"], str(data["description"]),
                tuple(data["statements"]),  # type: ignore[arg-type]
            )
        except Exception as exc:
            raise MigrationJournalCorruptionError("migration step is invalid") from exc


@dataclass(frozen=True)
class SchemaState:
    profile_id: str
    application_version: int
    lineage_version: str
    identity_sha256: str
    preservation_json: str


@dataclass(frozen=True)
class MigrationPlan:
    migration_id: str
    profile_id: str
    source_version: int
    target_version: int
    source_identity_sha256: str
    preservation_json: str
    snapshot_sha256: str
    snapshot_size_bytes: int
    snapshot_lineage_schema_version: str
    steps: tuple[MigrationStep, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "migration_id", _mig_id(self.migration_id))
        object.__setattr__(self, "profile_id", _profile(self.profile_id))
        for field in ("source_version", "target_version"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise MigrationPlanError(f"{field} must be positive")
        object.__setattr__(self, "source_identity_sha256", _sha(self.source_identity_sha256, "source_identity_sha256"))
        try:
            preservation = json.loads(str(self.preservation_json))
        except Exception as exc:
            raise MigrationPlanError("preservation_json is invalid") from exc
        object.__setattr__(self, "preservation_json", _json(preservation))
        object.__setattr__(self, "snapshot_sha256", _sha(self.snapshot_sha256, "snapshot_sha256"))
        if isinstance(self.snapshot_size_bytes, bool) or not isinstance(self.snapshot_size_bytes, int) or self.snapshot_size_bytes <= 0:
            raise MigrationPlanError("snapshot_size_bytes must be positive")
        object.__setattr__(self, "snapshot_lineage_schema_version", _text(self.snapshot_lineage_schema_version, "snapshot_lineage_schema_version"))
        steps = tuple(self.steps)
        if any(not isinstance(step, MigrationStep) for step in steps):
            raise TypeError("steps must contain MigrationStep")
        object.__setattr__(self, "steps", steps)

    def to_data(self) -> dict[str, object]:
        return {
            "migration_id": self.migration_id,
            "profile_id": self.profile_id,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "source_identity_sha256": self.source_identity_sha256,
            "preservation": json.loads(self.preservation_json),
            "snapshot_sha256": self.snapshot_sha256,
            "snapshot_size_bytes": self.snapshot_size_bytes,
            "snapshot_lineage_schema_version": self.snapshot_lineage_schema_version,
            "steps": [step.to_data() for step in self.steps],
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_data())

    @classmethod
    def from_data(cls, data: object) -> "MigrationPlan":
        if not isinstance(data, dict) or set(data) != {
            "migration_id", "profile_id", "source_version", "target_version",
            "source_identity_sha256", "preservation", "snapshot_sha256",
            "snapshot_size_bytes", "snapshot_lineage_schema_version", "steps",
        }:
            raise MigrationJournalCorruptionError("migration plan shape is invalid")
        try:
            return cls(
                migration_id=str(data["migration_id"]),
                profile_id=str(data["profile_id"]),
                source_version=data["source_version"],  # type: ignore[arg-type]
                target_version=data["target_version"],  # type: ignore[arg-type]
                source_identity_sha256=str(data["source_identity_sha256"]),
                preservation_json=_json(data["preservation"]),
                snapshot_sha256=str(data["snapshot_sha256"]),
                snapshot_size_bytes=data["snapshot_size_bytes"],  # type: ignore[arg-type]
                snapshot_lineage_schema_version=str(data["snapshot_lineage_schema_version"]),
                steps=tuple(MigrationStep.from_data(x) for x in data["steps"]),  # type: ignore[index]
            )
        except Exception as exc:
            if isinstance(exc, MigrationJournalCorruptionError):
                raise
            raise MigrationJournalCorruptionError("migration plan is invalid") from exc


@dataclass(frozen=True)
class MigrationHistoryEntry:
    sequence: int
    migration_id: str
    from_version: int
    to_version: int
    step_fingerprint: str
    description: str


@dataclass(frozen=True)
class MigrationResult:
    state: str
    plan: MigrationPlan
    installed_version: int | None
    restored_snapshot_sha256: str | None
    evidence: str


@dataclass(frozen=True)
class MigrationStatus:
    state: str
    plan: MigrationPlan
    evidence: str
    requires_recovery: bool
    retry_allowed: bool


@dataclass
class _Journal:
    plan: MigrationPlan
    state: str
    history: list[dict[str, str]]
    evidence: str

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": MIGRATION_SCHEMA_VERSION,
            "plan": self.plan.to_data(),
            "plan_fingerprint": self.plan.fingerprint,
            "state": self.state,
            "history": self.history,
            "evidence": self.evidence,
        }


class ApplicationSchemaMigrator:
    """Lease-bound, snapshot-backed staged migration of one stopped profile."""

    def __init__(self, data_root: str | Path, state_root: str | Path):
        self.data_root = _real_dir(data_root, "data_root")
        self.state_root = _real_dir(state_root, "state_root", create=True)
        self._leases = InstanceLeaseManager(self.state_root)

    def _db_path(self, profile_id: str) -> Path:
        return self.data_root / "profiles" / _profile(profile_id) / LineageStore.DB_NAME

    def _migration_dir(self, profile_id: str) -> Path:
        path = self.data_root / "profiles" / _profile(profile_id) / "migration"
        if path.is_symlink():
            raise MigrationJournalCorruptionError("migration directory must not be a symlink")
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise MigrationJournalCorruptionError("migration directory is not a real directory")
        return path

    def _journal_path(self, profile_id: str, migration_id: str) -> Path:
        return self._migration_dir(profile_id) / f"{_mig_id(migration_id)}.json"

    @staticmethod
    def _row_digest(
        conn: sqlite3.Connection,
        table: str,
        columns: tuple[str, ...],
        *,
        prefix_limit: int | None = None,
    ) -> tuple[int, str]:
        query = f"SELECT {','.join(_qi(c) for c in columns)} FROM {_qi(table)}"
        params: tuple[object, ...] = ()
        if table == "metadata" and "key" in columns:
            query += " WHERE key != ?"
            params = (APP_SCHEMA_KEY,)
        if table == "application_schema_migrations":
            query += " ORDER BY sequence"
            if prefix_limit is not None:
                query += " LIMIT ?"
                params += (prefix_limit,)
        rows = [
            _json([_sql_value(v) for v in tuple(row)])
            for row in conn.execute(query, params)
        ]
        rows.sort()
        return len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest()

    @classmethod
    def _capture_preservation(cls, conn: sqlite3.Connection) -> str:
        result: dict[str, object] = {}
        names = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for name in names:
            info = list(conn.execute(f"PRAGMA table_info({_qi(name)})"))
            if not info:
                raise MigrationValidationError(f"table {name} has no inspectable columns")
            columns = [
                [str(r[1]), str(r[2]), int(r[3]), _sql_value(r[4]), int(r[5])]
                for r in info
            ]
            fks = sorted(
                [list(_sql_value(v) for v in row) for row in conn.execute(f"PRAGMA foreign_key_list({_qi(name)})")],
                key=_json,
            )
            count, rows_sha = cls._row_digest(conn, name, tuple(c[0] for c in columns))
            result[name] = {
                "columns": columns,
                "foreign_keys": fks,
                "row_count": count,
                "rows_sha256": rows_sha,
            }
        return _json({"tables": result})

    @classmethod
    def _verify_preservation(cls, conn: sqlite3.Connection, preservation_json: str) -> None:
        try:
            expected = json.loads(preservation_json)["tables"]
        except Exception as exc:
            raise MigrationValidationError("preservation manifest is unreadable") from exc
        current_names = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        for name, spec in expected.items():
            if name not in current_names:
                raise MigrationValidationError(f"migration removed preserved table: {name}")
            info = list(conn.execute(f"PRAGMA table_info({_qi(name)})"))
            by_name = {
                str(r[1]): [str(r[1]), str(r[2]), int(r[3]), _sql_value(r[4]), int(r[5])]
                for r in info
            }
            for column in spec["columns"]:
                if by_name.get(column[0]) != column:
                    raise MigrationValidationError(
                        f"migration changed preserved column contract: {name}.{column[0]}"
                    )
            fks = sorted(
                [list(_sql_value(v) for v in row) for row in conn.execute(f"PRAGMA foreign_key_list({_qi(name)})")],
                key=_json,
            )
            if fks != spec["foreign_keys"]:
                raise MigrationValidationError(f"migration changed preserved foreign keys: {name}")
            columns = tuple(c[0] for c in spec["columns"])
            if name == "application_schema_migrations":
                total = int(conn.execute("SELECT COUNT(*) FROM application_schema_migrations").fetchone()[0])
                count, rows_sha = cls._row_digest(
                    conn, name, columns, prefix_limit=int(spec["row_count"])
                )
                if total < int(spec["row_count"]):
                    raise MigrationValidationError("migration deleted prior migration history")
            else:
                count, rows_sha = cls._row_digest(conn, name, columns)
            if count != int(spec["row_count"]) or rows_sha != spec["rows_sha256"]:
                raise MigrationValidationError(f"migration changed preserved semantic rows: {name}")

    @classmethod
    def _read_state(cls, conn: sqlite3.Connection, profile_id: str) -> SchemaState:
        conn.row_factory = sqlite3.Row
        quick = conn.execute("PRAGMA quick_check").fetchone()
        if quick is None or str(quick[0]) != "ok":
            raise MigrationValidationError("SQLite integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationValidationError("foreign-key integrity check failed")
        metadata = {
            str(r["key"]): str(r["value"])
            for r in conn.execute(
                "SELECT key,value FROM metadata WHERE key IN ('profile_id','schema_version',?)",
                (APP_SCHEMA_KEY,),
            )
        }
        if metadata.get("profile_id") != profile_id:
            raise MigrationValidationError("database profile identity mismatch")
        lineage_version = metadata.get("schema_version")
        if not lineage_version:
            raise MigrationValidationError("lineage schema version is missing")
        try:
            app_version = int(metadata.get(APP_SCHEMA_KEY, str(APP_SCHEMA_DEFAULT_VERSION)))
        except ValueError as exc:
            raise MigrationValidationError("application semantic schema version is invalid") from exc
        if app_version < 1:
            raise MigrationValidationError("application semantic schema version must be positive")
        identity: dict[str, list[tuple[object, ...]]] = {}
        for name, query in {
            "artists": "SELECT id,display_name FROM artists ORDER BY id",
            "songs": "SELECT id,artist_id,title,current_version_id,approved_version_id FROM songs ORDER BY id",
            "versions": "SELECT id,song_id,ordinal,label,parent_version_id FROM versions ORDER BY id",
            "assets": "SELECT id,song_id,name,sha256,source_uri FROM assets ORDER BY id",
            "version_assets": "SELECT version_id,asset_id,role FROM version_assets ORDER BY version_id,asset_id,role",
        }.items():
            identity[name] = [tuple(row) for row in conn.execute(query)]
        return SchemaState(
            profile_id,
            app_version,
            lineage_version,
            _digest(identity),
            cls._capture_preservation(conn),
        )

    @classmethod
    def _inspect(cls, path: Path, profile_id: str) -> SchemaState:
        if path.is_symlink() or not path.is_file():
            raise MigrationValidationError("profile database is missing/not a real file")
        conn = None
        try:
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
            conn.execute("PRAGMA query_only=ON")
            return cls._read_state(conn, profile_id)
        except MigrationValidationError:
            raise
        except sqlite3.DatabaseError as exc:
            raise MigrationValidationError("profile database is unreadable") from exc
        finally:
            if conn is not None:
                conn.close()

    @classmethod
    def _inspect_path(cls, path: Path, profile_id: str) -> SchemaState:
        # Stable test/recovery seam retained for crash fixtures.
        return cls._inspect(path, profile_id)

    @classmethod
    def _path_preserves(cls, path: Path, preservation_json: str) -> bool:
        conn = None
        try:
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
            conn.execute("PRAGMA query_only=ON")
            cls._verify_preservation(conn, preservation_json)
            return True
        except Exception:
            return False
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _validate_chain(source: int, target: int, steps: tuple[MigrationStep, ...]) -> None:
        if isinstance(target, bool) or not isinstance(target, int) or target < 1:
            raise MigrationPlanError("target_version must be positive")
        if target < source:
            raise MigrationPlanError("schema migration cannot silently downgrade")
        if target == source:
            if steps:
                raise MigrationPlanError("current-version plan must contain no steps")
            return
        expected = source
        for step in steps:
            if step.from_version != expected:
                raise MigrationPlanError("migration chain has a gap/duplicate/out-of-order step")
            expected = step.to_version
        if expected != target:
            raise MigrationPlanError("migration chain does not reach target_version")

    def _acquire(self, profile_id: str, process: ProcessIdentity, probe: ProcessProbe) -> InstanceLease:
        if not isinstance(process, ProcessIdentity):
            raise TypeError("process must be ProcessIdentity")
        result = self._leases.acquire(_profile(profile_id), process, probe)
        if result.status in {"ALREADY_OWNED", "HELD_BY_OTHER", "UNCERTAIN"}:
            raise MigrationBusyError(f"profile is not proven stopped/exclusive: {result.status}")
        if result.status not in {"ACQUIRED", "REPLACED_STALE"} or result.lease is None:
            raise MigrationBusyError(f"unexpected profile lease result: {result.status}")
        return result.lease

    def _release(self, profile_id: str, process: ProcessIdentity, lease: InstanceLease) -> None:
        self._leases.release(
            _profile(profile_id), process=process, lease_nonce=lease.lease_nonce
        )

    def _write_journal(self, journal: _Journal, *, create: bool = False) -> None:
        self._validate_journal(journal)
        path = self._journal_path(journal.plan.profile_id, journal.plan.migration_id)
        if path.is_symlink():
            raise MigrationJournalCorruptionError("migration journal must not be a symlink")
        if create and path.exists():
            raise MigrationJournalCorruptionError("migration journal already exists")
        payload = journal.payload()
        envelope = dict(payload)
        envelope["integrity_sha256"] = _digest(payload)
        encoded = (_json(envelope) + "\n").encode()
        temp = path.parent / f".{journal.plan.migration_id}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(temp, flags, 0o600)
        try:
            pos = 0
            while pos < len(encoded):
                wrote = os.write(fd, encoded[pos:])
                if wrote <= 0:
                    raise OSError("short migration-journal write")
                pos += wrote
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temp, path)
            _fsync_dir(path.parent)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _validate_journal(journal: _Journal) -> None:
        states = [entry["state"] for entry in journal.history]
        if (
            journal.state not in _STATES
            or not states
            or states[0] != "PREPARED"
            or states[-1] != journal.state
        ):
            raise MigrationJournalCorruptionError("migration journal endpoints are invalid")
        for previous, current in zip(states, states[1:]):
            if current not in _TRANSITIONS.get(previous, set()):
                raise MigrationJournalCorruptionError(
                    f"illegal migration transition: {previous}->{current}"
                )

    def _read_journal(self, profile_id: str, migration_id: str) -> _Journal:
        path = self._journal_path(profile_id, migration_id)
        if path.is_symlink() or not path.is_file():
            raise MigrationJournalCorruptionError("migration journal missing/not a real file")
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            raise MigrationJournalCorruptionError("migration journal unreadable") from exc
        if not isinstance(data, dict):
            raise MigrationJournalCorruptionError("migration journal must be an object")
        integrity = data.pop("integrity_sha256", None)
        if not isinstance(integrity, str) or not _SHA256.fullmatch(integrity) or _digest(data) != integrity:
            raise MigrationJournalCorruptionError("migration journal integrity mismatch")
        if set(data) != {"schema_version", "plan", "plan_fingerprint", "state", "history", "evidence"}:
            raise MigrationJournalCorruptionError("migration journal shape is invalid")
        if data["schema_version"] != MIGRATION_SCHEMA_VERSION:
            raise MigrationJournalCorruptionError("unsupported migration journal version")
        plan = MigrationPlan.from_data(data["plan"])
        if plan.profile_id != _profile(profile_id) or plan.migration_id != _mig_id(migration_id):
            raise MigrationJournalCorruptionError("migration journal identity mismatch")
        if data["plan_fingerprint"] != plan.fingerprint:
            raise MigrationJournalCorruptionError("migration plan fingerprint mismatch")
        if not isinstance(data["history"], list):
            raise MigrationJournalCorruptionError("migration history is invalid")
        try:
            history = [
                {"state": str(x["state"]), "evidence": _text(str(x["evidence"]), "history evidence")}
                for x in data["history"]
                if isinstance(x, dict) and set(x) == {"state", "evidence"}
            ]
        except Exception as exc:
            raise MigrationJournalCorruptionError("migration history is invalid") from exc
        if len(history) != len(data["history"]):
            raise MigrationJournalCorruptionError("migration history entry shape is invalid")
        journal = _Journal(plan, str(data["state"]), history, _text(str(data["evidence"]), "evidence"))
        self._validate_journal(journal)
        return journal

    def _transition(
        self, plan: MigrationPlan, *, expected: set[str], new_state: str, evidence: str
    ) -> _Journal:
        journal = self._read_journal(plan.profile_id, plan.migration_id)
        if journal.plan != plan:
            raise MigrationValidationError("supplied plan differs from durable journal")
        if journal.state not in expected:
            raise MigrationExecuteOnceError(
                f"migration state is {journal.state}, expected {sorted(expected)}"
            )
        if new_state not in _TRANSITIONS[journal.state]:
            raise MigrationJournalCorruptionError(
                f"illegal migration transition: {journal.state}->{new_state}"
            )
        journal.state = new_state
        journal.evidence = _text(evidence, "evidence")
        journal.history.append({"state": new_state, "evidence": journal.evidence})
        self._write_journal(journal)
        return journal

    def prepare(
        self,
        *,
        profile_id: str,
        target_version: int,
        steps: Iterable[MigrationStep],
        process: ProcessIdentity,
        probe: ProcessProbe,
    ) -> MigrationPlan:
        profile = _profile(profile_id)
        steps = tuple(steps)
        lease = self._acquire(profile, process, probe)
        plan = None
        primary = None
        try:
            live = self._inspect(self._db_path(profile), profile)
            self._validate_chain(live.application_version, target_version, steps)
            store = LineageStore.open(self.data_root, profile)
            try:
                snapshot: SnapshotInfo = RecoveryManager(store).create_snapshot()
            finally:
                store.close()
            snap = self._inspect(snapshot.path, profile)
            if (
                snap.identity_sha256 != live.identity_sha256
                or snap.preservation_json != live.preservation_json
            ):
                raise MigrationValidationError("recovery snapshot differs from live semantic state")
            plan = MigrationPlan(
                f"mig_{uuid.uuid4().hex}",
                profile,
                live.application_version,
                target_version,
                live.identity_sha256,
                live.preservation_json,
                snapshot.sha256,
                snapshot.size_bytes,
                snapshot.lineage_schema_version,
                steps,
            )
            self._write_journal(
                _Journal(
                    plan,
                    "PREPARED",
                    [{"state": "PREPARED", "evidence": f"snapshot:{snapshot.sha256}"}],
                    f"snapshot:{snapshot.sha256}",
                ),
                create=True,
            )
        except BaseException as exc:
            primary = exc
        release_error = None
        try:
            self._release(profile, process, lease)
        except BaseException as exc:
            release_error = exc
        if release_error is not None and plan is not None:
            try:
                self._transition(
                    plan,
                    expected={"PREPARED"},
                    new_state="RECOVERY_REQUIRED",
                    evidence=f"prepare lease release failed:{release_error}",
                )
            except Exception:
                pass
        if primary is not None:
            if release_error is not None:
                raise MigrationBusyError(
                    f"prepare failed and profile lease cleanup also failed:{release_error}"
                ) from primary
            raise primary
        if release_error is not None:
            raise MigrationBusyError(f"prepare could not release profile lease:{release_error}")
        if plan is None:
            raise SchemaMigrationError("migration preparation produced no plan")
        return plan

    def _verify_source(self, plan: MigrationPlan) -> None:
        live = self._inspect(self._db_path(plan.profile_id), plan.profile_id)
        if (
            live.application_version != plan.source_version
            or live.identity_sha256 != plan.source_identity_sha256
            or live.preservation_json != plan.preservation_json
        ):
            raise MigrationValidationError("live semantic state changed after migration preparation")
        snapshot = RecoveryManager.inspect_snapshot(self.data_root, plan.profile_id)
        if (
            snapshot.sha256 != plan.snapshot_sha256
            or snapshot.size_bytes != plan.snapshot_size_bytes
            or snapshot.lineage_schema_version != plan.snapshot_lineage_schema_version
        ):
            raise MigrationValidationError("prepared recovery snapshot changed/disappeared")
        snap = self._inspect(snapshot.path, plan.profile_id)
        if snap.identity_sha256 != plan.source_identity_sha256 or snap.preservation_json != plan.preservation_json:
            raise MigrationValidationError("prepared recovery snapshot semantic state changed")

    def _history_matches(self, plan: MigrationPlan, path: Path) -> bool:
        conn = None
        try:
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='application_schema_migrations'"
            ).fetchone()
            if exists is None:
                return False
            rows = [
                tuple(row)
                for row in conn.execute(
                    "SELECT from_version,to_version,step_fingerprint,description "
                    "FROM application_schema_migrations WHERE migration_id=? ORDER BY to_version",
                    (plan.migration_id,),
                )
            ]
            expected = [
                (s.from_version, s.to_version, s.fingerprint, s.description)
                for s in plan.steps
            ]
            return rows == expected
        except sqlite3.DatabaseError:
            return False
        finally:
            if conn is not None:
                conn.close()

    def _stage(self, plan: MigrationPlan, stage: Path) -> None:
        snapshot = RecoveryManager.inspect_snapshot(self.data_root, plan.profile_id)
        if stage.exists() or stage.is_symlink():
            stage.unlink(missing_ok=True)
        shutil.copyfile(snapshot.path, stage)
        conn = sqlite3.connect(stage)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT value FROM metadata WHERE key=?", (APP_SCHEMA_KEY,)).fetchone() is None:
                conn.execute(
                    "INSERT INTO metadata(key,value) VALUES(?,?)",
                    (APP_SCHEMA_KEY, str(APP_SCHEMA_DEFAULT_VERSION)),
                )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS application_schema_migrations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    migration_id TEXT NOT NULL,
                    from_version INTEGER NOT NULL,
                    to_version INTEGER NOT NULL,
                    step_fingerprint TEXT NOT NULL,
                    description TEXT NOT NULL,
                    UNIQUE(migration_id,to_version)
                )"""
            )
            version = plan.source_version
            for step in plan.steps:
                if step.from_version != version:
                    raise MigrationValidationError("step no longer matches staged version")
                for statement in step.statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO metadata(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (APP_SCHEMA_KEY, str(step.to_version)),
                )
                conn.execute(
                    "INSERT INTO application_schema_migrations"
                    "(migration_id,from_version,to_version,step_fingerprint,description)"
                    " VALUES(?,?,?,?,?)",
                    (plan.migration_id, step.from_version, step.to_version, step.fingerprint, step.description),
                )
                version = step.to_version
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()
        state = self._inspect(stage, plan.profile_id)
        if state.application_version != plan.target_version or state.identity_sha256 != plan.source_identity_sha256:
            raise MigrationValidationError("staged migration changed version/identity incorrectly")
        conn = sqlite3.connect(stage)
        try:
            conn.execute("PRAGMA query_only=ON")
            self._verify_preservation(conn, plan.preservation_json)
        finally:
            conn.close()
        if not self._history_matches(plan, stage):
            raise MigrationValidationError("staged migration history does not match exact plan")
        _fsync_file(stage)

    def _stage_from_snapshot(self, plan: MigrationPlan, stage: Path) -> None:
        # Stable crash-fixture seam; production and tests share the same staging path.
        self._stage(plan, stage)

    def _checkpoint_live(self, path_or_plan, plan: MigrationPlan | None = None) -> None:
        # Accept the historical crash-fixture signature (path, plan) while the
        # production path supplies only plan. The path is verified, never trusted.
        if plan is None:
            plan = path_or_plan
        else:
            supplied = Path(path_or_plan)
            expected = self._db_path(plan.profile_id)
            if supplied != expected:
                raise MigrationValidationError("checkpoint path differs from migration profile database")
        live = self._db_path(plan.profile_id)
        conn = sqlite3.connect(live)
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise MigrationValidationError("live database failed pre-install integrity check")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise MigrationValidationError("live database failed pre-install foreign-key check")
            checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is not None and int(checkpoint[0]) != 0:
                raise MigrationBusyError("live WAL could not be checkpointed exclusively")
        finally:
            conn.close()
        self._verify_source(plan)

    def _validate_installed(self, plan: MigrationPlan) -> None:
        headquarters = None
        validation_error = None
        try:
            headquarters = HeadquartersMemory.open(self.data_root, plan.profile_id)
            state = self._read_state(headquarters.store._conn, plan.profile_id)
            if state.application_version != plan.target_version or state.identity_sha256 != plan.source_identity_sha256:
                raise MigrationValidationError("installed target version/identity mismatch")
            self._verify_preservation(headquarters.store._conn, plan.preservation_json)
            if not self._history_matches(plan, self._db_path(plan.profile_id)):
                raise MigrationValidationError("installed migration history mismatch")
        except Exception as exc:
            validation_error = exc
        finally:
            if headquarters is not None:
                try:
                    headquarters.close()
                except Exception as close_exc:
                    raise _DatabaseReleaseUncertain(
                        "installed Headquarters could not prove database release"
                    ) from close_exc
        if validation_error is not None:
            raise validation_error

    def _settle(
        self,
        plan: MigrationPlan,
        *,
        process: ProcessIdentity,
        lease: InstanceLease,
        expected: set[str],
        state: str,
        evidence: str,
        installed_version: int | None,
        restored_sha256: str | None = None,
    ) -> MigrationResult:
        try:
            self._release(plan.profile_id, process, lease)
        except Exception as exc:
            journal = self._transition(
                plan, expected=expected, new_state="RECOVERY_REQUIRED",
                evidence=f"result known but profile lease release failed:{exc}",
            )
            return MigrationResult("RECOVERY_REQUIRED", plan, installed_version, restored_sha256, journal.evidence)
        journal = self._transition(plan, expected=expected, new_state=state, evidence=evidence)
        return MigrationResult(state, plan, installed_version, restored_sha256, journal.evidence)

    def _restore_from_restoring(
        self, plan: MigrationPlan, *, process: ProcessIdentity, lease: InstanceLease
    ) -> MigrationResult:
        try:
            restored = RecoveryManager.restore_snapshot(
                self.data_root, plan.profile_id, expected_sha256=plan.snapshot_sha256
            )
            source = self._inspect(self._db_path(plan.profile_id), plan.profile_id)
            if (
                source.application_version != plan.source_version
                or source.identity_sha256 != plan.source_identity_sha256
                or source.preservation_json != plan.preservation_json
            ):
                raise MigrationValidationError("restored database differs from exact prepared source")
        except Exception as exc:
            journal = self._transition(
                plan, expected={"RESTORING"}, new_state="RECOVERY_REQUIRED",
                evidence=f"snapshot restore failed:{exc}",
            )
            return MigrationResult("RECOVERY_REQUIRED", plan, None, None, journal.evidence)
        return self._settle(
            plan, process=process, lease=lease, expected={"RESTORING"}, state="ROLLED_BACK",
            evidence=f"restored exact snapshot:{restored.installed_sha256}",
            installed_version=plan.source_version, restored_sha256=restored.installed_sha256,
        )

    def _rollback_validation(
        self, plan: MigrationPlan, *, process: ProcessIdentity, lease: InstanceLease, reason: str
    ) -> MigrationResult:
        self._transition(
            plan, expected={"VALIDATING"}, new_state="RESTORING",
            evidence=f"installed validation failed:{reason}",
        )
        return self._restore_from_restoring(plan, process=process, lease=lease)

    def _recover_in_flight(
        self, plan: MigrationPlan, *, process: ProcessIdentity, lease: InstanceLease, state: str
    ) -> MigrationResult | None:
        live_path = self._db_path(plan.profile_id)
        try:
            live = self._inspect(live_path, plan.profile_id)
        except Exception as exc:
            journal = self._transition(
                plan, expected={state}, new_state="RECOVERY_REQUIRED",
                evidence=f"interrupted migration live database is not safely inspectable:{exc}",
            )
            return MigrationResult("RECOVERY_REQUIRED", plan, None, None, journal.evidence)
        source_exact = (
            live.application_version == plan.source_version
            and live.identity_sha256 == plan.source_identity_sha256
            and live.preservation_json == plan.preservation_json
        )
        target_exact = (
            live.application_version == plan.target_version
            and live.identity_sha256 == plan.source_identity_sha256
            and self._path_preserves(live_path, plan.preservation_json)
            and self._history_matches(plan, live_path)
        )
        if source_exact:
            if state == "STAGING":
                return None
            return self._settle(
                plan, process=process, lease=lease, expected={state},
                state="FAILED_SAFE" if state == "INSTALLING" else "ROLLED_BACK",
                evidence=(
                    "interrupted install left exact source state intact"
                    if state == "INSTALLING"
                    else "interrupted installed migration is back on exact source state"
                ),
                installed_version=plan.source_version,
                restored_sha256=(plan.snapshot_sha256 if state in {"VALIDATING", "RESTORING"} else None),
            )
        if target_exact:
            if state == "INSTALLING":
                self._transition(
                    plan, expected={"INSTALLING"}, new_state="VALIDATING",
                    evidence="interrupted install found exact target candidate installed",
                )
                state = "VALIDATING"
            if state == "VALIDATING":
                try:
                    self._validate_installed(plan)
                except _DatabaseReleaseUncertain as exc:
                    journal = self._transition(
                        plan, expected={"VALIDATING"}, new_state="RECOVERY_REQUIRED", evidence=str(exc)
                    )
                    return MigrationResult("RECOVERY_REQUIRED", plan, plan.target_version, None, journal.evidence)
                except Exception as exc:
                    return self._rollback_validation(plan, process=process, lease=lease, reason=str(exc))
                return self._settle(
                    plan, process=process, lease=lease, expected={"VALIDATING"}, state="SUCCEEDED",
                    evidence="interrupted install revalidated exact target semantic state",
                    installed_version=plan.target_version,
                )
            if state == "RESTORING":
                return self._restore_from_restoring(plan, process=process, lease=lease)
        journal = self._transition(
            plan, expected={state}, new_state="RECOVERY_REQUIRED",
            evidence="interrupted state matches neither exact source nor exact target",
        )
        return MigrationResult("RECOVERY_REQUIRED", plan, live.application_version, None, journal.evidence)

    def migrate(
        self, plan: MigrationPlan, *, process: ProcessIdentity, probe: ProcessProbe
    ) -> MigrationResult:
        if not isinstance(plan, MigrationPlan):
            raise TypeError("plan must be MigrationPlan")
        self._validate_chain(plan.source_version, plan.target_version, plan.steps)
        journal = self._read_journal(plan.profile_id, plan.migration_id)
        if journal.plan != plan:
            raise MigrationValidationError("supplied plan differs from durable journal")
        if journal.state in _TERMINAL:
            raise MigrationExecuteOnceError(f"migration is terminal:{journal.state}")
        lease = self._acquire(plan.profile_id, process, probe)
        try:
            journal = self._read_journal(plan.profile_id, plan.migration_id)
            if journal.state in _IN_FLIGHT:
                recovered = self._recover_in_flight(
                    plan, process=process, lease=lease, state=journal.state
                )
                if recovered is not None:
                    return recovered
                journal = self._read_journal(plan.profile_id, plan.migration_id)
            if journal.state not in {"PREPARED", "STAGING"}:
                raise MigrationExecuteOnceError(f"migration cannot start from:{journal.state}")
            self._verify_source(plan)
            if plan.target_version == plan.source_version:
                return self._settle(
                    plan, process=process, lease=lease, expected={journal.state}, state="NO_CHANGE",
                    evidence="profile already at exact target semantic schema",
                    installed_version=plan.source_version,
                )
            if journal.state == "PREPARED":
                self._transition(
                    plan, expected={"PREPARED"}, new_state="STAGING",
                    evidence="staging exact recovery-snapshot copy",
                )
            stage = self._migration_dir(plan.profile_id) / f"{plan.migration_id}.stage.sqlite3"
            try:
                self._stage(plan, stage)
            except Exception as exc:
                try:
                    self._release(plan.profile_id, process, lease)
                except Exception as release_exc:
                    journal = self._transition(
                        plan, expected={"STAGING"}, new_state="RECOVERY_REQUIRED",
                        evidence=f"staging failed and lease release also failed:{release_exc}",
                    )
                    return MigrationResult("RECOVERY_REQUIRED", plan, plan.source_version, None, journal.evidence)
                journal = self._transition(
                    plan, expected={"STAGING"}, new_state="FAILED_SAFE",
                    evidence=f"staged migration failed before live install:{exc}",
                )
                return MigrationResult("FAILED_SAFE", plan, plan.source_version, None, journal.evidence)
            self._checkpoint_live(plan)
            self._transition(
                plan, expected={"STAGING"}, new_state="INSTALLING",
                evidence="validated staged target; beginning atomic live install",
            )
            live = self._db_path(plan.profile_id)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(live) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            os.replace(stage, live)
            _fsync_file(live)
            _fsync_dir(live.parent)
            self._transition(
                plan, expected={"INSTALLING"}, new_state="VALIDATING",
                evidence="target atomically installed; reopening canonical Headquarters",
            )
            try:
                self._validate_installed(plan)
            except _DatabaseReleaseUncertain as exc:
                journal = self._transition(
                    plan, expected={"VALIDATING"}, new_state="RECOVERY_REQUIRED", evidence=str(exc)
                )
                return MigrationResult("RECOVERY_REQUIRED", plan, plan.target_version, None, journal.evidence)
            except Exception as exc:
                return self._rollback_validation(plan, process=process, lease=lease, reason=str(exc))
            return self._settle(
                plan, process=process, lease=lease, expected={"VALIDATING"}, state="SUCCEEDED",
                evidence="installed target reopened with exact semantic preservation",
                installed_version=plan.target_version,
            )
        except (MigrationPlanError, MigrationValidationError, MigrationExecuteOnceError, MigrationBusyError):
            try:
                self._release(plan.profile_id, process, lease)
            except Exception:
                pass
            raise
        except Exception as exc:
            current = self._read_journal(plan.profile_id, plan.migration_id)
            if current.state in _IN_FLIGHT:
                try:
                    current = self._transition(
                        plan, expected={current.state}, new_state="RECOVERY_REQUIRED",
                        evidence=f"migration coordinator failed after mutation may have started:{exc}",
                    )
                except Exception:
                    pass
            if current.state == "RECOVERY_REQUIRED":
                return MigrationResult("RECOVERY_REQUIRED", plan, None, None, current.evidence)
            try:
                self._release(plan.profile_id, process, lease)
            except Exception:
                pass
            raise
        finally:
            try:
                (self._migration_dir(plan.profile_id) / f"{plan.migration_id}.stage.sqlite3").unlink(missing_ok=True)
            except OSError:
                pass

    def status(self, profile_id: str, migration_id: str) -> MigrationStatus:
        journal = self._read_journal(profile_id, migration_id)
        return MigrationStatus(
            journal.state,
            journal.plan,
            journal.evidence,
            journal.state in _IN_FLIGHT or journal.state == "RECOVERY_REQUIRED",
            journal.state in {"PREPARED", "STAGING"},
        )

    def history(self, profile_id: str) -> tuple[MigrationHistoryEntry, ...]:
        profile = _profile(profile_id)
        path = self._db_path(profile)
        if path.is_symlink() or not path.is_file():
            raise MigrationValidationError("profile database missing/not a real file")
        conn = None
        try:
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            conn.execute("PRAGMA query_only=ON")
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='application_schema_migrations'"
            ).fetchone() is None:
                return ()
            return tuple(
                MigrationHistoryEntry(
                    int(row[0]), str(row[1]), int(row[2]), int(row[3]), str(row[4]), str(row[5])
                )
                for row in conn.execute(
                    "SELECT sequence,migration_id,from_version,to_version,step_fingerprint,description "
                    "FROM application_schema_migrations ORDER BY sequence"
                )
            )
        except sqlite3.DatabaseError as exc:
            raise MigrationValidationError("migration history unreadable") from exc
        finally:
            if conn is not None:
                conn.close()
