from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

import n0te2.migration as migration_module
from n0te2.instance import InstanceLeaseManager, ProcessIdentity
from n0te2.memory import HeadquartersMemory
from n0te2.migration import (
    ApplicationSchemaMigrator,
    MigrationBusyError,
    MigrationExecuteOnceError,
    MigrationJournalCorruptionError,
    MigrationPlanError,
    MigrationStep,
    MigrationValidationError,
)
from n0te2.platforms import PlatformEnvironment
from n0te2.recovery import RecoveryManager


class Probe:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, process: ProcessIdentity, state: str) -> None:
        self.values[process.fingerprint] = state

    def status(self, process: ProcessIdentity) -> str:
        return self.values.get(process.fingerprint, "UNKNOWN")


def process(pid: int = 1201, token: str = "schema-migration-owner") -> ProcessIdentity:
    return ProcessIdentity.from_start_token(
        PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
        pid=pid,
        start_token=token,
    )


def environment(tmp_path: Path):
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    headquarters = HeadquartersMemory.create(data_root, "Migration Artist")
    profile_id = headquarters.store.profile_id
    song = headquarters.store.create_song("Migration Song")
    version = headquarters.store.create_version(song.id, label="Before migration")
    headquarters.close()
    migrator = ApplicationSchemaMigrator(data_root, state_root)
    return data_root, state_root, profile_id, song, version, migrator, process(), Probe()


def database(data_root: Path, profile_id: str) -> Path:
    return data_root / "profiles" / profile_id / "lineage.sqlite3"


def application_version(data_root: Path, profile_id: str) -> int:
    conn = sqlite3.connect(database(data_root, profile_id))
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='application_semantic_schema_version'"
        ).fetchone()
        return 1 if row is None else int(row[0])
    finally:
        conn.close()


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def test_no_change_is_explicit_and_execute_once(tmp_path: Path) -> None:
    data_root, _, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=1,
        steps=(),
        process=proc,
        probe=probe,
    )
    result = migrator.migrate(plan, process=proc, probe=probe)
    assert result.state == "NO_CHANGE"
    assert result.installed_version == 1
    assert application_version(data_root, profile_id) == 1
    with pytest.raises(MigrationExecuteOnceError):
        migrator.migrate(plan, process=proc, probe=probe)


def test_contiguous_additive_chain_preserves_song_and_all_prior_rows(tmp_path: Path) -> None:
    data_root, _, profile_id, song, version, migrator, proc, probe = environment(tmp_path)
    before = HeadquartersMemory.open(data_root, profile_id)
    before_counts = {
        row[0]: before.store._conn.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0]
        for row in before.store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    before.close()

    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=3,
        steps=(
            MigrationStep(1, 2, "add note column", ("ALTER TABLE songs ADD COLUMN app_note TEXT",)),
            MigrationStep(2, 3, "add migration fixture", ("CREATE TABLE app_migration_fixture(id TEXT PRIMARY KEY, note TEXT)",)),
        ),
        process=proc,
        probe=probe,
    )
    result = migrator.migrate(plan, process=proc, probe=probe)
    assert result.state == "SUCCEEDED"
    assert application_version(data_root, profile_id) == 3

    reopened = HeadquartersMemory.open(data_root, profile_id)
    try:
        resumed = reopened.store.active_song()
        assert resumed is not None and resumed.id == song.id
        assert resumed.current_version_id == version.id
        for table, count in before_counts.items():
            assert reopened.store._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] >= count
    finally:
        reopened.close()
    history = migrator.history(profile_id)
    assert [(item.from_version, item.to_version) for item in history] == [(1, 2), (2, 3)]


def test_missing_or_out_of_order_chain_fails_before_install(tmp_path: Path) -> None:
    data_root, _, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    with pytest.raises(MigrationPlanError):
        migrator.prepare(
            profile_id=profile_id,
            target_version=3,
            steps=(MigrationStep(1, 2, "only first step", ("CREATE TABLE x(id TEXT)",)),),
            process=proc,
            probe=probe,
        )
    assert application_version(data_root, profile_id) == 1


def test_destructive_sql_requires_separate_consequential_decision() -> None:
    with pytest.raises(MigrationPlanError):
        MigrationStep(1, 2, "erase", ("DROP TABLE songs",))
    with pytest.raises(MigrationPlanError):
        MigrationStep(1, 2, "delete", ("DELETE FROM songs",))


def test_semantic_rewrite_of_existing_metadata_fails_safe_before_live_install(tmp_path: Path) -> None:
    data_root, _, profile_id, song, _, migrator, proc, probe = environment(tmp_path)
    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=2,
        steps=(
            MigrationStep(
                1,
                2,
                "attempt semantic rewrite",
                ("UPDATE metadata SET value='' WHERE key='active_song_id'",),
            ),
        ),
        process=proc,
        probe=probe,
    )
    result = migrator.migrate(plan, process=proc, probe=probe)
    assert result.state == "FAILED_SAFE"
    assert application_version(data_root, profile_id) == 1
    reopened = HeadquartersMemory.open(data_root, profile_id)
    try:
        assert reopened.store.active_song().id == song.id
    finally:
        reopened.close()


def test_failed_staged_sql_leaves_live_database_unchanged(tmp_path: Path) -> None:
    data_root, _, profile_id, song, _, migrator, proc, probe = environment(tmp_path)
    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=2,
        steps=(MigrationStep(1, 2, "bad stage", ("INSERT INTO table_that_does_not_exist VALUES(1)",)),),
        process=proc,
        probe=probe,
    )
    result = migrator.migrate(plan, process=proc, probe=probe)
    assert result.state == "FAILED_SAFE"
    assert application_version(data_root, profile_id) == 1
    reopened = HeadquartersMemory.open(data_root, profile_id)
    try:
        assert reopened.store.active_song().id == song.id
    finally:
        reopened.close()


def test_profile_lease_must_prove_stopped_exclusive_state(tmp_path: Path) -> None:
    _, state_root, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    leases = InstanceLeaseManager(state_root)
    acquired = leases.acquire(profile_id, proc, probe)
    assert acquired.status == "ACQUIRED" and acquired.lease is not None
    try:
        with pytest.raises(MigrationBusyError):
            migrator.prepare(
                profile_id=profile_id,
                target_version=2,
                steps=(MigrationStep(1, 2, "x", ("CREATE TABLE x(id TEXT)",)),),
                process=proc,
                probe=probe,
            )
    finally:
        leases.release(profile_id, process=proc, lease_nonce=acquired.lease.lease_nonce)


def test_snapshot_tamper_or_live_edit_after_prepare_blocks_migration(tmp_path: Path) -> None:
    data_root, _, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=2,
        steps=(MigrationStep(1, 2, "x", ("CREATE TABLE x(id TEXT)",)),),
        process=proc,
        probe=probe,
    )
    snapshot = RecoveryManager.snapshot_path(data_root, profile_id)
    snapshot.write_bytes(snapshot.read_bytes() + b"tamper")
    with pytest.raises(MigrationValidationError):
        migrator.migrate(plan, process=proc, probe=probe)

    data_root2, _, profile_id2, _, _, migrator2, proc2, probe2 = environment(tmp_path / "second")
    plan2 = migrator2.prepare(
        profile_id=profile_id2,
        target_version=2,
        steps=(MigrationStep(1, 2, "x", ("CREATE TABLE x(id TEXT)",)),),
        process=proc2,
        probe=probe2,
    )
    changed = HeadquartersMemory.open(data_root2, profile_id2)
    changed.store.create_song("Post-prepare edit")
    changed.close()
    with pytest.raises(MigrationValidationError):
        migrator2.migrate(plan2, process=proc2, probe=probe2)


def test_staging_crash_can_resume_from_exact_source(tmp_path: Path) -> None:
    _, _, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=2,
        steps=(MigrationStep(1, 2, "x", ("CREATE TABLE x(id TEXT)",)),),
        process=proc,
        probe=probe,
    )
    migrator._transition(plan, expected={"PREPARED"}, new_state="STAGING", evidence="simulated crash")
    status = migrator.status(profile_id, plan.migration_id)
    assert status.requires_recovery is True and status.retry_allowed is True
    assert migrator.migrate(plan, process=proc, probe=probe).state == "SUCCEEDED"


def test_installing_crash_with_source_intact_settles_failed_safe(tmp_path: Path) -> None:
    data_root, _, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=2,
        steps=(MigrationStep(1, 2, "x", ("CREATE TABLE x(id TEXT)",)),),
        process=proc,
        probe=probe,
    )
    migrator._transition(plan, expected={"PREPARED"}, new_state="STAGING", evidence="stage")
    migrator._transition(plan, expected={"STAGING"}, new_state="INSTALLING", evidence="install")
    status = migrator.status(profile_id, plan.migration_id)
    assert status.requires_recovery is True and status.retry_allowed is False
    result = migrator.migrate(plan, process=proc, probe=probe)
    assert result.state == "FAILED_SAFE"
    assert application_version(data_root, profile_id) == 1


def test_installing_crash_with_exact_target_resumes_validation(tmp_path: Path) -> None:
    data_root, _, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=2,
        steps=(MigrationStep(1, 2, "x", ("CREATE TABLE x(id TEXT)",)),),
        process=proc,
        probe=probe,
    )
    stage = migrator._migration_dir(profile_id) / f"{plan.migration_id}.stage.sqlite3"
    migrator._transition(plan, expected={"PREPARED"}, new_state="STAGING", evidence="stage")
    migrator._stage_from_snapshot(plan, stage)
    migrator._checkpoint_live(database(data_root, profile_id), plan)
    migrator._transition(plan, expected={"STAGING"}, new_state="INSTALLING", evidence="install")
    os.replace(stage, database(data_root, profile_id))
    result = migrator.migrate(plan, process=proc, probe=probe)
    assert result.state == "SUCCEEDED"
    assert application_version(data_root, profile_id) == 2


def test_post_install_open_failure_restores_exact_snapshot(tmp_path: Path, monkeypatch) -> None:
    data_root, _, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=2,
        steps=(MigrationStep(1, 2, "x", ("CREATE TABLE x(id TEXT)",)),),
        process=proc,
        probe=probe,
    )

    def fail_open(cls, root, wanted_profile):
        raise RuntimeError("simulated new build cannot open profile")

    monkeypatch.setattr(migration_module.HeadquartersMemory, "open", classmethod(fail_open))
    result = migrator.migrate(plan, process=proc, probe=probe)
    assert result.state == "ROLLED_BACK"
    assert result.restored_snapshot_sha256 == plan.snapshot_sha256
    assert application_version(data_root, profile_id) == 1


def test_validation_close_failure_is_recovery_required_without_restore(tmp_path: Path, monkeypatch) -> None:
    _, _, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=2,
        steps=(MigrationStep(1, 2, "x", ("CREATE TABLE x(id TEXT)",)),),
        process=proc,
        probe=probe,
    )
    original_open = migration_module.HeadquartersMemory.open
    opened = []

    def close_failing_open(cls, root, wanted_profile):
        headquarters = original_open(root, wanted_profile)
        opened.append(headquarters)
        headquarters.close = lambda: (_ for _ in ()).throw(RuntimeError("simulated close failure"))
        return headquarters

    monkeypatch.setattr(
        migration_module.HeadquartersMemory,
        "open",
        classmethod(close_failing_open),
    )
    result = migrator.migrate(plan, process=proc, probe=probe)
    assert result.state == "RECOVERY_REQUIRED"
    assert result.restored_snapshot_sha256 is None
    for headquarters in opened:
        headquarters.store.close()


def test_restore_failure_is_recovery_required(tmp_path: Path, monkeypatch) -> None:
    _, _, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=2,
        steps=(MigrationStep(1, 2, "x", ("CREATE TABLE x(id TEXT)",)),),
        process=proc,
        probe=probe,
    )

    def fail_open(cls, root, wanted_profile):
        raise RuntimeError("validation failed")

    def fail_restore(cls, root, wanted_profile, *, expected_sha256):
        raise RuntimeError("restore failed")

    monkeypatch.setattr(migration_module.HeadquartersMemory, "open", classmethod(fail_open))
    monkeypatch.setattr(
        migration_module.RecoveryManager,
        "restore_snapshot",
        classmethod(fail_restore),
    )
    result = migrator.migrate(plan, process=proc, probe=probe)
    assert result.state == "RECOVERY_REQUIRED"


def test_recomputed_illegal_journal_transition_is_corruption(tmp_path: Path) -> None:
    _, _, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    plan = migrator.prepare(
        profile_id=profile_id,
        target_version=2,
        steps=(MigrationStep(1, 2, "x", ("CREATE TABLE x(id TEXT)",)),),
        process=proc,
        probe=probe,
    )
    path = migrator._journal_path(profile_id, plan.migration_id)
    envelope = json.loads(path.read_text())
    envelope["state"] = "SUCCEEDED"
    envelope["history"].append({"state": "SUCCEEDED", "evidence": "tampered shortcut"})
    envelope["evidence"] = "tampered shortcut"
    payload = {key: value for key, value in envelope.items() if key != "integrity_sha256"}
    envelope["integrity_sha256"] = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    path.write_text(canonical_json(envelope) + "\n")
    with pytest.raises(MigrationJournalCorruptionError):
        migrator.status(profile_id, plan.migration_id)


def test_second_migration_preserves_prior_migration_history(tmp_path: Path) -> None:
    _, _, profile_id, _, _, migrator, proc, probe = environment(tmp_path)
    first = migrator.prepare(
        profile_id=profile_id,
        target_version=2,
        steps=(MigrationStep(1, 2, "first", ("CREATE TABLE first_added(id TEXT)",)),),
        process=proc,
        probe=probe,
    )
    assert migrator.migrate(first, process=proc, probe=probe).state == "SUCCEEDED"
    first_history = migrator.history(profile_id)
    second = migrator.prepare(
        profile_id=profile_id,
        target_version=3,
        steps=(MigrationStep(2, 3, "second", ("CREATE TABLE second_added(id TEXT)",)),),
        process=proc,
        probe=probe,
    )
    assert migrator.migrate(second, process=proc, probe=probe).state == "SUCCEEDED"
    history = migrator.history(profile_id)
    assert len(first_history) == 1
    assert len(history) == 2
    assert history[0].migration_id == first_history[0].migration_id
