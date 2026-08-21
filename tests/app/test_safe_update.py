from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from n0te2.app_runtime import ApplicationRuntime
from n0te2.artifacts import (
    ArtifactRecord,
    ManifestAuthenticityEvidence,
    ReleaseManifest,
)
from n0te2.instance import InstanceLeaseManager, ProcessIdentity
from n0te2.memory import HeadquartersMemory
from n0te2.platforms import PlatformEnvironment
from n0te2.safe_update import ApplicationUpdateCoordinator
from n0te2.support import SupportTarget
from n0te2.update import (
    PackageActionReceipt,
    UpdateExecuteOnceError,
    UpdateJournalCorruptionError,
    UpdateRejectedError,
)


class Probe:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, process: ProcessIdentity, state: str) -> None:
        self.values[process.fingerprint] = state

    def status(self, process: ProcessIdentity) -> str:
        return self.values.get(process.fingerprint, "UNKNOWN")


def process(pid: int = 901, token: str = "update-owner") -> ProcessIdentity:
    return ProcessIdentity.from_start_token(
        PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
        pid=pid,
        start_token=token,
    )


def release_fixture(payload: bytes = b"n0te2-release-v2"):
    target = SupportTarget.from_runtime_labels(os_name="Linux", machine="x86_64")
    record = ArtifactRecord(
        artifact_id="linux-x86_64-package",
        target_fingerprint=target.fingerprint,
        package_kind="TEST_PACKAGE",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    manifest = ReleaseManifest(
        release_id="release-v2",
        version="2.0.0",
        source_commit_sha="a" * 40,
        build_inputs_sha256="b" * 64,
        dependency_inventory_sha256="c" * 64,
        license_inventory_sha256="d" * 64,
        artifacts=(record,),
    )
    authenticity = ManifestAuthenticityEvidence(
        manifest_fingerprint=manifest.fingerprint,
        status="VERIFIED",
        verifier_id="test-platform-verifier",
        scheme="TEST-ONLY-VERIFIER-RECEIPT",
        evidence_ref="evidence:manifest-authenticity",
    )
    return target, record, manifest, authenticity, payload


class Driver:
    def __init__(
        self,
        *,
        install_state: str = "SUCCEEDED",
        rollback_state: str = "SUCCEEDED",
        install_release: str | None = "release-v2",
        rollback_release: str | None = "release-v1",
    ) -> None:
        self.install_state = install_state
        self.rollback_state = rollback_state
        self.install_release = install_release
        self.rollback_release = rollback_release
        self.calls: list[str] = []

    def perform(self, request, artifact_bytes):
        self.calls.append(request.action)
        if request.action == "INSTALL":
            return PackageActionReceipt(
                request_fingerprint=request.fingerprint,
                action="INSTALL",
                state=self.install_state,
                evidence_ref=f"driver:install:{self.install_state.lower()}",
                resulting_release_id=self.install_release,
            )
        return PackageActionReceipt(
            request_fingerprint=request.fingerprint,
            action="ROLLBACK",
            state=self.rollback_state,
            evidence_ref=f"driver:rollback:{self.rollback_state.lower()}",
            resulting_release_id=self.rollback_release,
        )


def running_profile(tmp_path: Path):
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    bootstrap = HeadquartersMemory.create(data_root, "Update Artist")
    profile_id = bootstrap.store.profile_id
    bootstrap.close()
    proc = process()
    probe = Probe()
    runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert runtime.launch(profile_id=profile_id, process=proc, probe=probe).status == "STARTED"
    song = runtime.headquarters.store.create_song("Update Safety Song")
    version = runtime.headquarters.store.create_version(song.id, label="Before update")
    return data_root, state_root, profile_id, proc, probe, runtime, song, version


def prepared_update(tmp_path: Path, *, memory_opener=HeadquartersMemory.open):
    data_root, state_root, profile_id, proc, probe, runtime, song, version = running_profile(tmp_path)
    target, record, manifest, authenticity, payload = release_fixture()
    coordinator = ApplicationUpdateCoordinator(
        state_root=state_root,
        memory_opener=memory_opener,
    )
    plan = coordinator.prepare(
        runtime=runtime,
        current_release_id="release-v1",
        manifest=manifest,
        artifact_id=record.artifact_id,
        artifact_bytes=payload,
        target=target,
        authenticity=authenticity,
        process=proc,
        probe=probe,
    )
    assert runtime.state == "STOPPED"
    return (
        data_root,
        state_root,
        profile_id,
        proc,
        probe,
        coordinator,
        plan,
        target,
        record,
        manifest,
        authenticity,
        payload,
        song,
        version,
    )


def apply(coordinator, plan, data_root, manifest, payload, target, authenticity, driver, proc, probe):
    return coordinator.apply(
        plan=plan,
        data_root=data_root,
        manifest=manifest,
        artifact_bytes=payload,
        target=target,
        authenticity=authenticity,
        driver=driver,
        process=proc,
        probe=probe,
    )


def test_success_requires_real_headquarters_reopen_and_preserves_song(tmp_path: Path) -> None:
    values = prepared_update(tmp_path)
    data_root, state_root, profile_id, proc, probe, coordinator, plan, target, _, manifest, auth, payload, song, version = values
    driver = Driver()

    result = apply(coordinator, plan, data_root, manifest, payload, target, auth, driver, proc, probe)
    assert result.state == "SUCCEEDED"
    assert driver.calls == ["INSTALL"]
    assert coordinator.status(plan.update_id).retry_allowed is False
    assert InstanceLeaseManager(state_root).inspect(profile_id) is None

    runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert runtime.launch(profile_id=profile_id, process=proc, probe=probe).status == "STARTED"
    resumed = runtime.headquarters.store.active_song()
    assert resumed is not None
    assert resumed.id == song.id
    assert resumed.current_version_id == version.id
    assert runtime.quit().status == "STOPPED"


def test_failed_changed_package_rolls_back_and_restores_exact_snapshot(tmp_path: Path) -> None:
    values = prepared_update(tmp_path)
    data_root, _, _, proc, probe, coordinator, plan, target, _, manifest, auth, payload, _, _ = values
    driver = Driver(install_state="FAILED_CHANGED", install_release=None)

    result = apply(coordinator, plan, data_root, manifest, payload, target, auth, driver, proc, probe)
    assert result.state == "ROLLED_BACK"
    assert result.restored_sha256 == plan.snapshot_sha256
    assert driver.calls == ["INSTALL", "ROLLBACK"]


def test_validation_failure_triggers_package_rollback_and_snapshot_restore(tmp_path: Path) -> None:
    def bad_open(root, profile_id):
        raise RuntimeError("simulated updated build cannot open Headquarters")

    values = prepared_update(tmp_path, memory_opener=bad_open)
    data_root, _, _, proc, probe, coordinator, plan, target, _, manifest, auth, payload, _, _ = values
    driver = Driver()

    result = apply(coordinator, plan, data_root, manifest, payload, target, auth, driver, proc, probe)
    assert result.state == "ROLLED_BACK"
    assert driver.calls == ["INSTALL", "ROLLBACK"]
    assert result.restored_sha256 == plan.snapshot_sha256


def test_unknown_install_is_recovery_required_and_never_retried(tmp_path: Path) -> None:
    values = prepared_update(tmp_path)
    data_root, state_root, profile_id, proc, probe, coordinator, plan, target, _, manifest, auth, payload, _, _ = values
    driver = Driver(install_state="UNKNOWN", install_release=None)

    result = apply(coordinator, plan, data_root, manifest, payload, target, auth, driver, proc, probe)
    assert result.state == "RECOVERY_REQUIRED"
    status = coordinator.status(plan.update_id)
    assert status.requires_recovery is True
    assert status.retry_allowed is False
    assert driver.calls == ["INSTALL"]
    assert InstanceLeaseManager(state_root).inspect(profile_id) is not None
    with pytest.raises(UpdateExecuteOnceError):
        apply(coordinator, plan, data_root, manifest, payload, target, auth, driver, proc, probe)


def test_wrong_success_release_receipt_becomes_ambiguous_recovery(tmp_path: Path) -> None:
    values = prepared_update(tmp_path)
    data_root, _, _, proc, probe, coordinator, plan, target, _, manifest, auth, payload, _, _ = values
    driver = Driver(install_release="some-other-release")

    result = apply(coordinator, plan, data_root, manifest, payload, target, auth, driver, proc, probe)
    assert result.state == "RECOVERY_REQUIRED"
    assert driver.calls == ["INSTALL"]


def test_rollback_failure_does_not_attempt_snapshot_restore(tmp_path: Path) -> None:
    values = prepared_update(tmp_path)
    data_root, _, _, proc, probe, coordinator, plan, target, _, manifest, auth, payload, _, _ = values
    driver = Driver(
        install_state="FAILED_CHANGED",
        install_release=None,
        rollback_state="FAILED",
        rollback_release=None,
    )

    result = apply(coordinator, plan, data_root, manifest, payload, target, auth, driver, proc, probe)
    assert result.state == "RECOVERY_REQUIRED"
    assert result.restored_sha256 is None
    assert driver.calls == ["INSTALL", "ROLLBACK"]


def test_song_change_after_prepare_refuses_before_package_callback(tmp_path: Path) -> None:
    values = prepared_update(tmp_path)
    data_root, _, profile_id, proc, probe, coordinator, plan, target, _, manifest, auth, payload, _, _ = values
    reopened = HeadquartersMemory.open(data_root, profile_id)
    reopened.store.create_song("Sneaky post-prepare edit")
    reopened.close()
    driver = Driver()

    with pytest.raises(UpdateRejectedError):
        apply(coordinator, plan, data_root, manifest, payload, target, auth, driver, proc, probe)
    assert driver.calls == []
    assert coordinator.status(plan.update_id).state == "PREPARED"


def test_existing_prepared_update_blocks_second_update_for_same_profile(tmp_path: Path) -> None:
    values = prepared_update(tmp_path)
    data_root, state_root, profile_id, proc, probe, coordinator, _, target, record, manifest, auth, payload, _, _ = values
    runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert runtime.launch(profile_id=profile_id, process=proc, probe=probe).status == "STARTED"

    with pytest.raises(UpdateRejectedError):
        coordinator.prepare(
            runtime=runtime,
            current_release_id="release-v1",
            manifest=manifest,
            artifact_id=record.artifact_id,
            artifact_bytes=payload,
            target=target,
            authenticity=auth,
            process=proc,
            probe=probe,
        )
    assert runtime.state == "RUNNING"
    assert runtime.quit().status == "STOPPED"


def test_illegal_recomputed_history_is_rejected_as_corruption(tmp_path: Path) -> None:
    values = prepared_update(tmp_path)
    _, _, _, _, _, coordinator, plan, _, _, _, _, _, _, _ = values
    path = coordinator.updates_root / f"{plan.update_id}.json"
    envelope = json.loads(path.read_text())
    envelope["state"] = "SUCCEEDED"
    envelope["history"].append({"state": "SUCCEEDED", "evidence": "tampered:shortcut"})
    payload = {key: value for key, value in envelope.items() if key != "integrity_sha256"}
    envelope["integrity_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(UpdateJournalCorruptionError):
        coordinator.status(plan.update_id)


def test_crash_breadcrumb_is_recovery_not_retry_permission(tmp_path: Path) -> None:
    values = prepared_update(tmp_path)
    _, _, _, _, _, coordinator, plan, _, _, _, _, _, _, _ = values
    coordinator._transition(
        plan.update_id,
        expected={"PREPARED"},
        new_state="INSTALLING",
        evidence="test:simulated-crash-after-start",
    )
    status = coordinator.status(plan.update_id)
    assert status.state == "INSTALLING"
    assert status.requires_recovery is True
    assert status.retry_allowed is False


def test_validation_close_failure_does_not_auto_rollback_under_open_database(tmp_path: Path) -> None:
    opened: list[HeadquartersMemory] = []

    def close_failing_open(root, profile_id):
        headquarters = HeadquartersMemory.open(root, profile_id)
        opened.append(headquarters)
        def fail_close():
            raise RuntimeError("simulated close failure")
        headquarters.close = fail_close  # type: ignore[method-assign]
        return headquarters

    values = prepared_update(tmp_path, memory_opener=close_failing_open)
    data_root, _, _, proc, probe, coordinator, plan, target, _, manifest, auth, payload, _, _ = values
    driver = Driver()

    result = apply(coordinator, plan, data_root, manifest, payload, target, auth, driver, proc, probe)
    assert result.state == "RECOVERY_REQUIRED"
    assert driver.calls == ["INSTALL"]
    for headquarters in opened:
        headquarters.store.close()
