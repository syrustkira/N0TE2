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
MIGRATION_STATES = {"NO_CHANGE", "SUCCEEDED", "ROLLED_BACK", "RECOVERY_REQUIRED"}
_PROFILE_ID = re.compile(r"^prf_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIGRATION_ID = re.compile(r"^mig_[0-9a-f]{32}$")
_FORBIDDEN_SQL = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|ATTACH|DETACH|VACUUM|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE)\b|WRITABLE_SCHEMA",
    re.IGNORECASE,
)


class SchemaMigrationError(RuntimeError):
    """Unsafe, incomplete or failed application semantic-schema migration."""


class MigrationPlanError(SchemaMigrationError):
    pass


class MigrationValidationError(SchemaMigrationError):
    pass


def _text(value: str, field: str) -> str:
    text = " ".join(str(value).split())
    if not text:
        raise MigrationPlanError(f"{field} must not be empty")
    return text


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


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


@dataclass(frozen=True)
class MigrationStep:
    from_version: int
    to_version: int
    description: str
    statements: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.from_version, bool) or not isinstance(self.from_version, int):
            raise MigrationPlanError("from_version must be an integer")
        if self.from_version < 1:
            raise MigrationPlanError("from_version must be a positive integer")
        if isinstance(self.to_version, bool) or not isinstance(self.to_version, int):
            raise MigrationPlanError("to_version must be an integer")
        if self.to_version != self.from_version + 1:
            raise MigrationPlanError("migration steps must advance exactly one version")
        object.__setattr__(self, "description", _text(self.description, "description"))
        statements = tuple(_text(statement, "migration SQL") for statement in self.statements)
        if not statements:
            raise MigrationPlanError("migration step requires at least one SQL statement")
        for statement in statements:
            if _FORBIDDEN_SQL.search(statement):
                raise MigrationPlanError(
                    "destructive, attached-database, transaction-control or writable-schema SQL "
                    "requires a separate consequential product decision"
                )
        object.__setattr__(self, "statements", statements)

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "from": self.from_version,
                "to": self.to_version,
                "description": self.description,
                "statements": self.statements,
            }
        )


@dataclass(frozen=True)
class SchemaState:
    profile_id: str
    application_version: int
    lineage_version: str
    identity_fingerprint: str


@dataclass(frozen=True)
class MigrationPlan:
    migration_id: str
    profile_id: str
    source_version: int
    target_version: int
    source_identity_fingerprint: str
    snapshot_sha256: str
    snapshot_size_bytes: int
    steps: tuple[MigrationStep, ...]

    def __post_init__(self) -> None:
        if not _MIGRATION_ID.fullmatch(str(self.migration_id)):
            raise MigrationPlanError("invalid migration_id")
        if not _PROFILE_ID.fullmatch(str(self.profile_id)):
            raise MigrationPlanError("invalid profile_id")
        if isinstance(self.source_version, bool) or not isinstance(self.source_version, int):
            raise MigrationPlanError("source_version must be an integer")
        if isinstance(self.target_version, bool) or not isinstance(self.target_version, int):
            raise MigrationPlanError("target_version must be an integer")
        if self.source_version < 1 or self.target_version < 1:
            raise MigrationPlanError("schema versions must be positive")
        if not _SHA256.fullmatch(str(self.source_identity_fingerprint)):
            raise MigrationPlanError("source identity fingerprint must be SHA-256")
        if not _SHA256.fullmatch(str(self.snapshot_sha256)):
            raise MigrationPlanError("snapshot_sha256 must be SHA-256")
        if isinstance(self.snapshot_size_bytes, bool) or not isinstance(self.snapshot_size_bytes, int):
            raise MigrationPlanError("snapshot_size_bytes must be an integer")
        if self.snapshot_size_bytes <= 0:
            raise MigrationPlanError("snapshot_size_bytes must be positive")
        object.__setattr__(self, "steps", tuple(self.steps))
        ApplicationSchemaMigrator._validate_chain(
            self.source_version, self.target_version, self.steps
        )

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "migration_id": self.migration_id,
                "profile_id": self.profile_id,
                "source_version": self.source_version,
                "target_version": self.target_version,
                "source_identity_fingerprint": self.source_identity_fingerprint,
                "snapshot_sha256": self.snapshot_sha256,
                "snapshot_size_bytes": self.snapshot_size_bytes,
                "steps": [step.fingerprint for step in self.steps],
            }
        )


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

    def __post_init__(self) -> None:
        if self.state not in MIGRATION_STATES:
            raise MigrationValidationError(f"invalid migration result state: {self.state}")


class ApplicationSchemaMigrator:
    """Snapshot-backed staged semantic-schema migration for one stopped profile.

    Preparation is read-only except for creating the canonical RecoveryManager snapshot.
    Execution takes the shared profile instance lease as maintenance ownership before any
    staged work. Normal runtime ownership, unknown ownership, and same-process runtime
    ownership all fail closed rather than trusting a caller-supplied STOPPED flag.
    """

    def __init__(self, data_root: str | Path, state_root: str | Path):
        data = Path(data_root)
        state = Path(state_root)
        if not data.is_absolute():
            raise MigrationPlanError("data_root must be absolute")
        if not state.is_absolute():
            raise MigrationPlanError("state_root must be absolute")
        self.data_root = Path(os.path.abspath(os.path.normpath(str(data))))
        self.state_root = Path(os.path.abspath(os.path.normpath(str(state))))
        self._leases = InstanceLeaseManager(self.state_root)

    def _db_path(self, profile_id: str) -> Path:
        if not _PROFILE_ID.fullmatch(str(profile_id)):
            raise MigrationPlanError("invalid profile_id")
        return self.data_root / "profiles" / str(profile_id) / LineageStore.DB_NAME

    @staticmethod
    def _read_state_from_connection(conn: sqlite3.Connection, profile_id: str) -> SchemaState:
        conn.row_factory = sqlite3.Row
        quick = conn.execute("PRAGMA quick_check").fetchone()
        if quick is None or str(quick[0]) != "ok":
            raise MigrationValidationError("SQLite integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationValidationError("foreign-key integrity check failed")
        metadata = {
            str(row["key"]): str(row["value"])
            for row in conn.execute(
                "SELECT key,value FROM metadata WHERE key IN "
                "('profile_id','schema_version','primary_artist_id','active_song_id',?)",
                (APP_SCHEMA_KEY,),
            )
        }
        if metadata.get("profile_id") != profile_id:
            raise MigrationValidationError("database profile identity mismatch")
        lineage_version = metadata.get("schema_version")
        primary_artist_id = metadata.get("primary_artist_id")
        if not lineage_version:
            raise MigrationValidationError("lineage schema version is missing")
        if not primary_artist_id:
            raise MigrationValidationError("primary Artist identity metadata is missing")
        if "active_song_id" not in metadata:
            raise MigrationValidationError("active Song identity metadata is missing")
        raw_app = metadata.get(APP_SCHEMA_KEY, str(APP_SCHEMA_DEFAULT_VERSION))
        try:
            app_version = int(raw_app)
        except ValueError as exc:
            raise MigrationValidationError("application semantic schema version is invalid") from exc
        if app_version < 1:
            raise MigrationValidationError("application semantic schema version must be positive")

        identity: dict[str, object] = {
            "metadata": {
                "profile_id": profile_id,
                "primary_artist_id": primary_artist_id,
                "active_song_id": metadata["active_song_id"],
                "lineage_schema_version": lineage_version,
            }
        }
        queries = {
            "artists": "SELECT id,display_name FROM artists ORDER BY id",
            "songs": "SELECT id,artist_id,title,current_version_id,approved_version_id FROM songs ORDER BY id",
            "versions": "SELECT id,song_id,ordinal,label,parent_version_id FROM versions ORDER BY id",
            "assets": "SELECT id,song_id,name,sha256,source_uri FROM assets ORDER BY id",
            "version_assets": "SELECT version_id,asset_id,role FROM version_assets ORDER BY version_id,asset_id,role",
        }
        for name, query in queries.items():
            identity[name] = [tuple(row) for row in conn.execute(query)]
        return SchemaState(
            profile_id=profile_id,
            application_version=app_version,
            lineage_version=lineage_version,
            identity_fingerprint=_digest(identity),
        )

    @classmethod
    def _inspect_path(cls, path: Path, profile_id: str) -> SchemaState:
        if path.is_symlink() or not path.is_file():
            raise MigrationValidationError("profile database is missing or not a real file")
        conn: sqlite3.Connection | None = None
        try:
            uri = path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            conn.execute("PRAGMA query_only=ON")
            return cls._read_state_from_connection(conn, profile_id)
        except sqlite3.DatabaseError as exc:
            raise MigrationValidationError("profile database is unreadable") from exc
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _validate_chain(source: int, target: int, steps: tuple[MigrationStep, ...]) -> None:
        if target < source:
            raise MigrationPlanError("schema migration cannot silently downgrade")
        if target == source:
            if steps:
                raise MigrationPlanError("current-version plan must contain no migration steps")
            return
        expected = source
        seen_edges: set[tuple[int, int]] = set()
        for step in steps:
            edge = (step.from_version, step.to_version)
            if edge in seen_edges or step.from_version != expected:
                raise MigrationPlanError("migration chain has a gap, duplicate or out-of-order step")
            seen_edges.add(edge)
            expected = step.to_version
        if expected != target:
            raise MigrationPlanError("migration chain does not reach target_version")

    def _assert_no_runtime_lease(self, profile_id: str) -> None:
        lease = self._leases.inspect(profile_id)
        if lease is not None:
            raise MigrationPlanError(
                "profile has runtime ownership; schema migration preparation requires no active lease"
            )

    def prepare(
        self,
        *,
        profile_id: str,
        target_version: int,
        steps: Iterable[MigrationStep],
    ) -> MigrationPlan:
        if isinstance(target_version, bool) or not isinstance(target_version, int):
            raise MigrationPlanError("target_version must be an integer")
        if target_version < 1:
            raise MigrationPlanError("target_version must be positive")
        self._assert_no_runtime_lease(profile_id)
        live = self._db_path(profile_id)
        source = self._inspect_path(live, profile_id)
        step_tuple = tuple(steps)
        self._validate_chain(source.application_version, target_version, step_tuple)

        store = LineageStore.open(self.data_root, profile_id)
        try:
            snapshot: SnapshotInfo = RecoveryManager(store).create_snapshot()
        finally:
            store.close()
        self._assert_no_runtime_lease(profile_id)
        latest = self._inspect_path(live, profile_id)
        if latest != source:
            raise MigrationValidationError("profile changed while migration was being prepared")
        return MigrationPlan(
            migration_id=f"mig_{uuid.uuid4().hex}",
            profile_id=profile_id,
            source_version=source.application_version,
            target_version=target_version,
            source_identity_fingerprint=source.identity_fingerprint,
            snapshot_sha256=snapshot.sha256,
            snapshot_size_bytes=snapshot.size_bytes,
            steps=step_tuple,
        )

    def history(self, profile_id: str) -> tuple[MigrationHistoryEntry, ...]:
        live = self._db_path(profile_id)
        self._inspect_path(live, profile_id)
        conn: sqlite3.Connection | None = None
        try:
            uri = live.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='application_schema_migrations'"
            ).fetchone()
            if exists is None:
                return ()
            rows = conn.execute(
                "SELECT sequence,migration_id,from_version,to_version,step_fingerprint,description "
                "FROM application_schema_migrations ORDER BY sequence"
            ).fetchall()
            return tuple(
                MigrationHistoryEntry(
                    sequence=int(row["sequence"]),
                    migration_id=str(row["migration_id"]),
                    from_version=int(row["from_version"]),
                    to_version=int(row["to_version"]),
                    step_fingerprint=str(row["step_fingerprint"]),
                    description=str(row["description"]),
                )
                for row in rows
            )
        except sqlite3.DatabaseError as exc:
            raise MigrationValidationError("migration history is unreadable") from exc
        finally:
            if conn is not None:
                conn.close()

    def migrate(
        self,
        plan: MigrationPlan,
        *,
        maintenance_process: ProcessIdentity,
        probe: ProcessProbe,
    ) -> MigrationResult:
        if not isinstance(plan, MigrationPlan):
            raise TypeError("plan must be MigrationPlan")
        if not isinstance(maintenance_process, ProcessIdentity):
            raise TypeError("maintenance_process must be ProcessIdentity")
        if not callable(getattr(probe, "status", None)):
            raise TypeError("probe must implement status(process)")
        self._validate_chain(plan.source_version, plan.target_version, plan.steps)
        live = self._db_path(plan.profile_id)
        current = self._inspect_path(live, plan.profile_id)
        if current.application_version != plan.source_version:
            raise MigrationValidationError("live application schema version changed after preparation")
        if current.identity_fingerprint != plan.source_identity_fingerprint:
            raise MigrationValidationError("Artist/Song identity changed after migration preparation")
        snapshot = RecoveryManager.inspect_snapshot(self.data_root, plan.profile_id)
        if snapshot.sha256 != plan.snapshot_sha256 or snapshot.size_bytes != plan.snapshot_size_bytes:
            raise MigrationValidationError("prepared recovery snapshot changed or disappeared")
        if plan.target_version == plan.source_version:
            return MigrationResult(
                "NO_CHANGE", plan, plan.source_version, None, "already at target schema; no migration write performed"
            )

        acquired = self._leases.acquire(plan.profile_id, maintenance_process, probe)
        if acquired.status not in {"ACQUIRED", "REPLACED_STALE"} or acquired.lease is None:
            raise MigrationPlanError(
                f"profile is not safely stopped for maintenance migration: {acquired.status}"
            )
        maintenance_lease: InstanceLease = acquired.lease

        try:
            result = self._migrate_owned(plan, maintenance_lease)
        except Exception as migration_exc:
            try:
                self._leases.release(
                    plan.profile_id,
                    process=maintenance_process,
                    lease_nonce=maintenance_lease.lease_nonce,
                )
            except Exception as release_exc:
                raise SchemaMigrationError(
                    f"migration failed ({migration_exc}) and maintenance lease release also failed ({release_exc})"
                ) from migration_exc
            raise
        try:
            self._leases.release(
                plan.profile_id,
                process=maintenance_process,
                lease_nonce=maintenance_lease.lease_nonce,
            )
        except Exception as release_exc:
            return MigrationResult(
                "RECOVERY_REQUIRED",
                plan,
                result.installed_version,
                result.restored_snapshot_sha256,
                f"{result.evidence}; migration maintenance lease release failed: {release_exc}",
            )
        return result

    def _migrate_owned(self, plan: MigrationPlan, maintenance_lease: InstanceLease) -> MigrationResult:
        live = self._db_path(plan.profile_id)
        owned = self._leases.inspect(plan.profile_id)
        if owned != maintenance_lease:
            raise MigrationValidationError("exact migration maintenance lease ownership was lost")
        current = self._inspect_path(live, plan.profile_id)
        if current.application_version != plan.source_version:
            raise MigrationValidationError("live schema version changed before staged migration")
        if current.identity_fingerprint != plan.source_identity_fingerprint:
            raise MigrationValidationError("live canonical identity changed before staged migration")
        snapshot = RecoveryManager.inspect_snapshot(self.data_root, plan.profile_id)
        if snapshot.sha256 != plan.snapshot_sha256 or snapshot.size_bytes != plan.snapshot_size_bytes:
            raise MigrationValidationError("prepared recovery snapshot changed before staged migration")

        profile_dir = live.parent
        migration_dir = profile_dir / "migration"
        migration_dir.mkdir(parents=True, exist_ok=True)
        stage = profile_dir / f".{LineageStore.DB_NAME}.{plan.migration_id}.stage"
        preserved = migration_dir / f"lineage.pre-migration.{plan.migration_id}.sqlite3"
        moved_sidecars: list[tuple[Path, Path]] = []
        source_conn: sqlite3.Connection | None = None
        stage_conn: sqlite3.Connection | None = None
        installed = False
        try:
            if stage.exists() or stage.is_symlink():
                raise MigrationValidationError("migration stage path already exists")
            source_uri = live.resolve().as_uri() + "?mode=ro"
            source_conn = sqlite3.connect(source_uri, uri=True, timeout=5.0)
            stage_conn = sqlite3.connect(stage, timeout=5.0)
            source_conn.backup(stage_conn)
            source_conn.close()
            source_conn = None
            stage_conn.execute("PRAGMA foreign_keys=ON")
            stage_conn.execute("BEGIN IMMEDIATE")
            existing = stage_conn.execute(
                "SELECT value FROM metadata WHERE key=?", (APP_SCHEMA_KEY,)
            ).fetchone()
            if existing is None:
                stage_conn.execute(
                    "INSERT INTO metadata(key,value) VALUES(?,?)",
                    (APP_SCHEMA_KEY, str(APP_SCHEMA_DEFAULT_VERSION)),
                )
            stage_conn.execute(
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
                    raise MigrationValidationError("migration step no longer matches staged version")
                for statement in step.statements:
                    stage_conn.execute(statement)
                stage_conn.execute(
                    "INSERT INTO metadata(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (APP_SCHEMA_KEY, str(step.to_version)),
                )
                stage_conn.execute(
                    "INSERT INTO application_schema_migrations"
                    "(migration_id,from_version,to_version,step_fingerprint,description) "
                    "VALUES(?,?,?,?,?)",
                    (
                        plan.migration_id,
                        step.from_version,
                        step.to_version,
                        step.fingerprint,
                        step.description,
                    ),
                )
                version = step.to_version
            stage_conn.commit()
            stage_conn.close()
            stage_conn = None

            candidate = self._inspect_path(stage, plan.profile_id)
            if candidate.application_version != plan.target_version:
                raise MigrationValidationError("staged migration did not reach target version")
            if candidate.identity_fingerprint != plan.source_identity_fingerprint:
                raise MigrationValidationError("staged migration changed canonical Artist/Song identity")
            _fsync_file(stage)

            if self._leases.inspect(plan.profile_id) != maintenance_lease:
                raise MigrationValidationError("migration maintenance ownership changed before install")
            latest = self._inspect_path(live, plan.profile_id)
            if latest.application_version != plan.source_version:
                raise MigrationValidationError("live schema version changed before install")
            if latest.identity_fingerprint != plan.source_identity_fingerprint:
                raise MigrationValidationError("live canonical identity changed before install")
            snapshot = RecoveryManager.inspect_snapshot(self.data_root, plan.profile_id)
            if snapshot.sha256 != plan.snapshot_sha256 or snapshot.size_bytes != plan.snapshot_size_bytes:
                raise MigrationValidationError("prepared recovery snapshot changed before install")

            shutil.copyfile(live, preserved)
            _fsync_file(preserved)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(live) + suffix)
                if sidecar.exists():
                    saved = Path(str(preserved) + suffix)
                    os.replace(sidecar, saved)
                    moved_sidecars.append((sidecar, saved))
            try:
                os.replace(stage, live)
                installed = True
            except Exception:
                for original, saved in reversed(moved_sidecars):
                    if saved.exists() and not original.exists():
                        os.replace(saved, original)
                raise
            _fsync_file(live)
            _fsync_dir(profile_dir)

            try:
                headquarters = HeadquartersMemory.open(self.data_root, plan.profile_id)
                try:
                    final_state = self._read_state_from_connection(
                        headquarters.store._conn, plan.profile_id
                    )
                    if final_state.application_version != plan.target_version:
                        raise MigrationValidationError("installed database reports wrong target version")
                    if final_state.identity_fingerprint != plan.source_identity_fingerprint:
                        raise MigrationValidationError("installed migration changed canonical identity")
                finally:
                    headquarters.close()
            except Exception as validation_exc:
                try:
                    restored = RecoveryManager.restore_snapshot(
                        self.data_root,
                        plan.profile_id,
                        expected_sha256=plan.snapshot_sha256,
                    )
                except Exception as restore_exc:
                    return MigrationResult(
                        "RECOVERY_REQUIRED",
                        plan,
                        None,
                        None,
                        f"installed migration validation failed ({validation_exc}) and exact snapshot restore also failed ({restore_exc})",
                    )
                return MigrationResult(
                    "ROLLED_BACK",
                    plan,
                    plan.source_version,
                    restored.installed_sha256,
                    f"installed migration validation failed and exact pre-migration snapshot was restored: {validation_exc}",
                )
            return MigrationResult(
                "SUCCEEDED",
                plan,
                plan.target_version,
                None,
                "staged migration preserved canonical identity, installed atomically and reopened Headquarters successfully",
            )
        except (MigrationPlanError, MigrationValidationError):
            raise
        except Exception as exc:
            if not installed:
                raise SchemaMigrationError(f"staged migration failed before install: {exc}") from exc
            try:
                restored = RecoveryManager.restore_snapshot(
                    self.data_root,
                    plan.profile_id,
                    expected_sha256=plan.snapshot_sha256,
                )
            except Exception as restore_exc:
                return MigrationResult(
                    "RECOVERY_REQUIRED",
                    plan,
                    None,
                    None,
                    f"post-install migration failure ({exc}) and exact snapshot restore also failed ({restore_exc})",
                )
            return MigrationResult(
                "ROLLED_BACK",
                plan,
                plan.source_version,
                restored.installed_sha256,
                f"post-install migration failure was compensated by exact snapshot restore: {exc}",
            )
        finally:
            if source_conn is not None:
                source_conn.close()
            if stage_conn is not None:
                try:
                    stage_conn.rollback()
                except Exception:
                    pass
                stage_conn.close()
            try:
                stage.unlink(missing_ok=True)
            except OSError:
                pass
