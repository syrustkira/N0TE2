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

from .lineage import LineageStore
from .memory import HeadquartersMemory
from .recovery import RecoveryManager, SnapshotInfo

APP_SCHEMA_KEY = "application_semantic_schema_version"
APP_SCHEMA_DEFAULT_VERSION = 1
_PROFILE_ID = re.compile(r"^prf_[0-9a-f]{32}$")
_FORBIDDEN_SQL = ("DROP ", "DELETE ", "TRUNCATE ", "ATTACH ", "DETACH ", "VACUUM", "WRITABLE_SCHEMA")


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
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


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
        if isinstance(self.from_version, bool) or not isinstance(self.from_version, int) or self.from_version < 1:
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
            upper = " ".join(statement.upper().split())
            if any(token in upper for token in _FORBIDDEN_SQL):
                raise MigrationPlanError(
                    "destructive/attached-database migration SQL requires a separate consequential product decision"
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
class MigrationResult:
    state: str
    plan: MigrationPlan
    installed_version: int | None
    restored_snapshot_sha256: str | None
    evidence: str


class ApplicationSchemaMigrator:
    """Snapshot-backed staged semantic-schema migration for one stopped profile."""

    def __init__(self, data_root: str | Path):
        root = Path(data_root)
        if not root.is_absolute():
            raise MigrationPlanError("data_root must be absolute")
        self.data_root = Path(os.path.abspath(os.path.normpath(str(root))))

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
                "SELECT key,value FROM metadata WHERE key IN ('profile_id','schema_version',?)",
                (APP_SCHEMA_KEY,),
            )
        }
        if metadata.get("profile_id") != profile_id:
            raise MigrationValidationError("database profile identity mismatch")
        lineage_version = metadata.get("schema_version")
        if not lineage_version:
            raise MigrationValidationError("lineage schema version is missing")
        raw_app = metadata.get(APP_SCHEMA_KEY, str(APP_SCHEMA_DEFAULT_VERSION))
        try:
            app_version = int(raw_app)
        except ValueError as exc:
            raise MigrationValidationError("application semantic schema version is invalid") from exc
        if app_version < 1:
            raise MigrationValidationError("application semantic schema version must be positive")

        identity: dict[str, list[tuple[object, ...]]] = {}
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
        for step in steps:
            if step.from_version != expected:
                raise MigrationPlanError("migration chain has a gap, duplicate or out-of-order step")
            expected = step.to_version
        if expected != target:
            raise MigrationPlanError("migration chain does not reach target_version")

    def prepare(
        self,
        *,
        profile_id: str,
        target_version: int,
        steps: Iterable[MigrationStep],
        runtime_state: str,
    ) -> MigrationPlan:
        if str(runtime_state).strip().upper() != "STOPPED":
            raise MigrationPlanError("schema migration requires a STOPPED application profile")
        live = self._db_path(profile_id)
        source = self._inspect_path(live, profile_id)
        step_tuple = tuple(steps)
        self._validate_chain(source.application_version, target_version, step_tuple)

        store = LineageStore.open(self.data_root, profile_id)
        try:
            snapshot: SnapshotInfo = RecoveryManager(store).create_snapshot()
        finally:
            store.close()
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

    def migrate(self, plan: MigrationPlan) -> MigrationResult:
        if not isinstance(plan, MigrationPlan):
            raise TypeError("plan must be MigrationPlan")
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
            return MigrationResult("NO_CHANGE", plan, plan.source_version, None, "already at target schema")

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
                    "INSERT INTO application_schema_migrations(migration_id,from_version,to_version,step_fingerprint,description) VALUES(?,?,?,?,?)",
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
                        f"installed migration validation failed ({validation_exc}) and snapshot restore also failed ({restore_exc})",
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
                "staged migration preserved canonical identity and reopened successfully",
            )
        except (MigrationPlanError, MigrationValidationError):
            raise
        except Exception as exc:
            if not installed:
                raise SchemaMigrationError(f"staged migration failed before install: {exc}") from exc
            return MigrationResult(
                "RECOVERY_REQUIRED",
                plan,
                None,
                None,
                f"migration outcome is ambiguous after install: {exc}",
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
