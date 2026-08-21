from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .app_runtime import ApplicationRuntime
from .artifacts import (
    ManifestAuthenticityEvidence,
    ReleaseArtifactVerifier,
    ReleaseManifest,
)
from .instance import InstanceLeaseManager, ProcessIdentity, ProcessProbe
from .recovery import RecoveryManager, RestoreResult, SnapshotInfo
from .support import SupportTarget

UPDATE_SCHEMA_VERSION = 1
UPDATE_STATES = {
    "PREPARED",
    "INSTALLING",
    "VALIDATING",
    "ROLLING_BACK",
    "RESTORING",
    "SUCCEEDED",
    "FAILED_SAFE",
    "ROLLED_BACK",
    "RECOVERY_REQUIRED",
}
TERMINAL_UPDATE_STATES = {
    "SUCCEEDED",
    "FAILED_SAFE",
    "ROLLED_BACK",
    "RECOVERY_REQUIRED",
}
PACKAGE_ACTIONS = {"INSTALL", "ROLLBACK"}
PACKAGE_INSTALL_STATES = {"SUCCEEDED", "FAILED_SAFE", "FAILED_CHANGED", "UNKNOWN"}
PACKAGE_ROLLBACK_STATES = {"SUCCEEDED", "FAILED", "UNKNOWN"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UPDATE_ID = re.compile(r"^upd_[0-9a-f]{32}$")


class ApplicationUpdateError(RuntimeError):
    """Base failure for application update orchestration."""


class UpdateRejectedError(ApplicationUpdateError):
    """The update could not safely enter package mutation."""


class UpdateExecuteOnceError(ApplicationUpdateError):
    """A prepared update is no longer eligible for first execution."""


class UpdateJournalCorruptionError(ApplicationUpdateError):
    """Durable update state is malformed, tampered or internally inconsistent."""


class UpdateBusyError(ApplicationUpdateError):
    """Another process/execution already owns this update transaction."""


def _text(value: str, field: str) -> str:
    text = " ".join(str(value).split())
    if not text:
        raise ApplicationUpdateError(f"{field} must not be empty")
    return text


def _sha256(value: str, field: str) -> str:
    text = str(value).strip().lower()
    if not _SHA256.fullmatch(text):
        raise ApplicationUpdateError(f"{field} must be a lowercase SHA-256")
    return text


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class UpdatePlan:
    update_id: str
    profile_id: str
    current_release_id: str
    target_release_id: str
    target_version: str
    manifest_fingerprint: str
    artifact_id: str
    artifact_sha256: str
    target_fingerprint: str
    snapshot_sha256: str
    snapshot_size_bytes: int
    snapshot_lineage_schema_version: str

    def __post_init__(self) -> None:
        update_id = str(self.update_id).strip().lower()
        if not _UPDATE_ID.fullmatch(update_id):
            raise ApplicationUpdateError("invalid update_id")
        object.__setattr__(self, "update_id", update_id)
        for field in (
            "profile_id",
            "current_release_id",
            "target_release_id",
            "target_version",
            "artifact_id",
            "snapshot_lineage_schema_version",
        ):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        for field in (
            "manifest_fingerprint",
            "artifact_sha256",
            "target_fingerprint",
            "snapshot_sha256",
        ):
            object.__setattr__(self, field, _sha256(getattr(self, field), field))
        if (
            isinstance(self.snapshot_size_bytes, bool)
            or not isinstance(self.snapshot_size_bytes, int)
            or self.snapshot_size_bytes <= 0
        ):
            raise ApplicationUpdateError("snapshot_size_bytes must be a positive integer")

    def to_data(self) -> dict[str, object]:
        return {
            "update_id": self.update_id,
            "profile_id": self.profile_id,
            "current_release_id": self.current_release_id,
            "target_release_id": self.target_release_id,
            "target_version": self.target_version,
            "manifest_fingerprint": self.manifest_fingerprint,
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
            "target_fingerprint": self.target_fingerprint,
            "snapshot_sha256": self.snapshot_sha256,
            "snapshot_size_bytes": self.snapshot_size_bytes,
            "snapshot_lineage_schema_version": self.snapshot_lineage_schema_version,
        }

    @classmethod
    def from_data(cls, data: object) -> "UpdatePlan":
        if not isinstance(data, dict):
            raise UpdateJournalCorruptionError("update plan must be an object")
        required = {
            "update_id",
            "profile_id",
            "current_release_id",
            "target_release_id",
            "target_version",
            "manifest_fingerprint",
            "artifact_id",
            "artifact_sha256",
            "target_fingerprint",
            "snapshot_sha256",
            "snapshot_size_bytes",
            "snapshot_lineage_schema_version",
        }
        if set(data) != required:
            raise UpdateJournalCorruptionError("update plan shape is invalid")
        try:
            return cls(**data)  # type: ignore[arg-type]
        except ApplicationUpdateError as exc:
            raise UpdateJournalCorruptionError("update plan is invalid") from exc

    @property
    def fingerprint(self) -> str:
        return _digest_json(self.to_data())


@dataclass(frozen=True)
class PackageActionRequest:
    update_id: str
    plan_fingerprint: str
    action: str
    profile_id: str
    current_release_id: str
    target_release_id: str
    artifact_id: str
    artifact_sha256: str
    target_fingerprint: str

    def __post_init__(self) -> None:
        if not _UPDATE_ID.fullmatch(str(self.update_id).strip().lower()):
            raise ApplicationUpdateError("invalid package request update_id")
        object.__setattr__(self, "update_id", str(self.update_id).strip().lower())
        object.__setattr__(self, "plan_fingerprint", _sha256(self.plan_fingerprint, "plan_fingerprint"))
        action = _text(self.action, "action").upper()
        if action not in PACKAGE_ACTIONS:
            raise ApplicationUpdateError(f"invalid package action: {action}")
        object.__setattr__(self, "action", action)
        for field in (
            "profile_id",
            "current_release_id",
            "target_release_id",
            "artifact_id",
        ):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(self, "artifact_sha256", _sha256(self.artifact_sha256, "artifact_sha256"))
        object.__setattr__(self, "target_fingerprint", _sha256(self.target_fingerprint, "target_fingerprint"))

    @property
    def fingerprint(self) -> str:
        return _digest_json(
            {
                "update_id": self.update_id,
                "plan_fingerprint": self.plan_fingerprint,
                "action": self.action,
                "profile_id": self.profile_id,
                "current_release_id": self.current_release_id,
                "target_release_id": self.target_release_id,
                "artifact_id": self.artifact_id,
                "artifact_sha256": self.artifact_sha256,
                "target_fingerprint": self.target_fingerprint,
            }
        )


@dataclass(frozen=True)
class PackageActionReceipt:
    request_fingerprint: str
    action: str
    state: str
    evidence_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_fingerprint",
            _sha256(self.request_fingerprint, "request_fingerprint"),
        )
        action = _text(self.action, "action").upper()
        if action not in PACKAGE_ACTIONS:
            raise ApplicationUpdateError(f"invalid package receipt action: {action}")
        object.__setattr__(self, "action", action)
        state = _text(self.state, "state").upper()
        allowed = PACKAGE_INSTALL_STATES if action == "INSTALL" else PACKAGE_ROLLBACK_STATES
        if state not in allowed:
            raise ApplicationUpdateError(f"invalid {action} receipt state: {state}")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "evidence_ref", _text(self.evidence_ref, "evidence_ref"))

    def to_data(self) -> dict[str, str]:
        return {
            "request_fingerprint": self.request_fingerprint,
            "action": self.action,
            "state": self.state,
            "evidence_ref": self.evidence_ref,
        }

    @classmethod
    def from_data(cls, data: object) -> "PackageActionReceipt":
        if not isinstance(data, dict) or set(data) != {
            "request_fingerprint",
            "action",
            "state",
            "evidence_ref",
        }:
            raise UpdateJournalCorruptionError("package receipt shape is invalid")
        try:
            return cls(**data)  # type: ignore[arg-type]
        except ApplicationUpdateError as exc:
            raise UpdateJournalCorruptionError("package receipt is invalid") from exc


class PackageDriver(Protocol):
    def perform(
        self,
        request: PackageActionRequest,
        artifact_bytes: bytes | None,
    ) -> PackageActionReceipt: ...


@dataclass(frozen=True)
class UpdateResult:
    state: str
    plan: UpdatePlan
    install_receipt: PackageActionReceipt | None
    rollback_receipt: PackageActionReceipt | None
    restored_sha256: str | None
    reason: str

    def __post_init__(self) -> None:
        if self.state not in TERMINAL_UPDATE_STATES:
            raise ApplicationUpdateError(f"invalid terminal update state: {self.state}")
        if self.restored_sha256 is not None:
            object.__setattr__(
                self,
                "restored_sha256",
                _sha256(self.restored_sha256, "restored_sha256"),
            )
        object.__setattr__(self, "reason", _text(self.reason, "reason"))


@dataclass(frozen=True)
class UpdateStatus:
    state: str
    plan: UpdatePlan
    install_receipt: PackageActionReceipt | None
    rollback_receipt: PackageActionReceipt | None
    restored_sha256: str | None
    reason: str | None


@dataclass
class _Journal:
    plan: UpdatePlan
    state: str
    history: list[dict[str, str]]
    install_receipt: PackageActionReceipt | None = None
    rollback_receipt: PackageActionReceipt | None = None
    restored_sha256: str | None = None
    reason: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": UPDATE_SCHEMA_VERSION,
            "plan": self.plan.to_data(),
            "plan_fingerprint": self.plan.fingerprint,
            "state": self.state,
            "history": self.history,
            "install_receipt": (
                None if self.install_receipt is None else self.install_receipt.to_data()
            ),
            "rollback_receipt": (
                None if self.rollback_receipt is None else self.rollback_receipt.to_data()
            ),
            "restored_sha256": self.restored_sha256,
            "reason": self.reason,
        }


class ApplicationUpdateCoordinator:
    """Durable shared update transaction above replaceable platform package drivers."""

    def __init__(self, *, state_root: str | Path, runtime_factory=ApplicationRuntime):
        root = Path(state_root)
        if not root.is_absolute():
            raise ApplicationUpdateError("state_root must be absolute")
        if not callable(runtime_factory):
            raise TypeError("runtime_factory must be callable")
        self.state_root = root
        self.runtime_factory = runtime_factory
        self._leases = InstanceLeaseManager(root)

    @property
    def updates_root(self) -> Path:
        return self.state_root / "updates"

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            try:
                os.fsync(fd)
            except OSError:
                pass
        finally:
            os.close(fd)

    def _prepare_updates_dir(self) -> None:
        root = self.updates_root
        if root.is_symlink():
            raise UpdateJournalCorruptionError("updates state root must not be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise UpdateJournalCorruptionError("updates state root is not a real directory")

    def _journal_path(self, update_id: str) -> Path:
        if not _UPDATE_ID.fullmatch(str(update_id).strip().lower()):
            raise ApplicationUpdateError("invalid update_id")
        return self.updates_root / f"{str(update_id).strip().lower()}.json"

    @staticmethod
    def _journal_integrity(payload: dict[str, object]) -> str:
        return _digest_json(payload)

    def _write_journal(self, journal: _Journal, *, create: bool = False) -> None:
        if journal.state not in UPDATE_STATES:
            raise ApplicationUpdateError(f"invalid journal state: {journal.state}")
        self._prepare_updates_dir()
        path = self._journal_path(journal.plan.update_id)
        if path.is_symlink():
            raise UpdateJournalCorruptionError("update journal must not be a symlink")
        if create and path.exists():
            raise UpdateJournalCorruptionError("update journal already exists")
        payload = journal.payload()
        envelope = dict(payload)
        envelope["integrity_sha256"] = self._journal_integrity(payload)
        encoded = (_canonical_json(envelope) + "\n").encode("utf-8")
        temp = self.updates_root / f".{journal.plan.update_id}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(temp, flags, 0o600)
        try:
            total = 0
            while total < len(encoded):
                written = os.write(fd, encoded[total:])
                if written <= 0:
                    raise OSError("short write while persisting update journal")
                total += written
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temp, path)
            self._fsync_dir(self.updates_root)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _read_journal(self, update_id: str) -> _Journal:
        path = self._journal_path(update_id)
        if path.is_symlink() or not path.is_file():
            raise UpdateJournalCorruptionError("update journal is missing or not a real file")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise UpdateJournalCorruptionError("update journal is unreadable") from exc
        if not isinstance(data, dict):
            raise UpdateJournalCorruptionError("update journal must be an object")
        integrity = data.pop("integrity_sha256", None)
        if not isinstance(integrity, str) or not _SHA256.fullmatch(integrity):
            raise UpdateJournalCorruptionError("update journal integrity digest is invalid")
        if self._journal_integrity(data) != integrity:
            raise UpdateJournalCorruptionError("update journal integrity mismatch")
        required = {
            "schema_version",
            "plan",
            "plan_fingerprint",
            "state",
            "history",
            "install_receipt",
            "rollback_receipt",
            "restored_sha256",
            "reason",
        }
        if set(data) != required or data["schema_version"] != UPDATE_SCHEMA_VERSION:
            raise UpdateJournalCorruptionError("update journal shape/version is invalid")
        plan = UpdatePlan.from_data(data["plan"])
        if data["plan_fingerprint"] != plan.fingerprint:
            raise UpdateJournalCorruptionError("update journal plan fingerprint mismatch")
        state = str(data["state"])
        if state not in UPDATE_STATES:
            raise UpdateJournalCorruptionError("update journal state is invalid")
        history = data["history"]
        if not isinstance(history, list) or not history:
            raise UpdateJournalCorruptionError("update journal history is invalid")
        expected_states = []
        for entry in history:
            if not isinstance(entry, dict) or set(entry) != {"state", "evidence"}:
                raise UpdateJournalCorruptionError("update journal history entry is invalid")
            entry_state = str(entry["state"])
            if entry_state not in UPDATE_STATES:
                raise UpdateJournalCorruptionError("update journal history state is invalid")
            _text(str(entry["evidence"]), "history evidence")
            expected_states.append(entry_state)
        if expected_states[-1] != state:
            raise UpdateJournalCorruptionError("update journal history/current state mismatch")
        install = (
            None
            if data["install_receipt"] is None
            else PackageActionReceipt.from_data(data["install_receipt"])
        )
        rollback = (
            None
            if data["rollback_receipt"] is None
            else PackageActionReceipt.from_data(data["rollback_receipt"])
        )
        restored = data["restored_sha256"]
        if restored is not None:
            restored = _sha256(str(restored), "restored_sha256")
        reason = data["reason"]
        if reason is not None:
            reason = _text(str(reason), "reason")
        return _Journal(
            plan=plan,
            state=state,
            history=[{"state": str(x["state"]), "evidence": str(x["evidence"])} for x in history],
            install_receipt=install,
            rollback_receipt=rollback,
            restored_sha256=restored,
            reason=reason,
        )

    def _transition(
        self,
        update_id: str,
        *,
        expected: set[str],
        new_state: str,
        evidence: str,
        install_receipt: PackageActionReceipt | None = None,
        rollback_receipt: PackageActionReceipt | None = None,
        restored_sha256: str | None = None,
        reason: str | None = None,
    ) -> _Journal:
        journal = self._read_journal(update_id)
        if journal.state not in expected:
            raise UpdateExecuteOnceError(
                f"update state is {journal.state}, expected one of {sorted(expected)}"
            )
        journal.state = new_state
        journal.history.append({"state": new_state, "evidence": _text(evidence, "evidence")})
        if install_receipt is not None:
            journal.install_receipt = install_receipt
        if rollback_receipt is not None:
            journal.rollback_receipt = rollback_receipt
        if restored_sha256 is not None:
            journal.restored_sha256 = _sha256(restored_sha256, "restored_sha256")
        if reason is not None:
            journal.reason = _text(reason, "reason")
        self._write_journal(journal)
        return journal

    @staticmethod
    def _request(plan: UpdatePlan, action: str) -> PackageActionRequest:
        return PackageActionRequest(
            update_id=plan.update_id,
            plan_fingerprint=plan.fingerprint,
            action=action,
            profile_id=plan.profile_id,
            current_release_id=plan.current_release_id,
            target_release_id=plan.target_release_id,
            artifact_id=plan.artifact_id,
            artifact_sha256=plan.artifact_sha256,
            target_fingerprint=plan.target_fingerprint,
        )

    @staticmethod
    def _validate_receipt(
        receipt: PackageActionReceipt,
        request: PackageActionRequest,
    ) -> PackageActionReceipt:
        if not isinstance(receipt, PackageActionReceipt):
            raise ApplicationUpdateError("package driver returned invalid receipt type")
        if receipt.action != request.action:
            raise ApplicationUpdateError("package receipt action does not match request")
        if receipt.request_fingerprint != request.fingerprint:
            raise ApplicationUpdateError("package receipt belongs to a different request")
        return receipt

    @staticmethod
    def _verify_artifact(
        *,
        plan: UpdatePlan | None,
        manifest: ReleaseManifest,
        artifact_id: str,
        artifact_bytes: bytes,
        target: SupportTarget,
        authenticity: ManifestAuthenticityEvidence,
    ):
        verification = ReleaseArtifactVerifier.verify(
            manifest=manifest,
            artifact_id=artifact_id,
            artifact_bytes=artifact_bytes,
            expected_target=target,
            authenticity=authenticity,
        )
        if verification.status != "READY" or verification.actual_sha256 is None:
            raise UpdateRejectedError(
                f"release artifact is not eligible for update: {verification.status}"
            )
        if plan is not None:
            if manifest.fingerprint != plan.manifest_fingerprint:
                raise UpdateRejectedError("manifest fingerprint differs from prepared update")
            if manifest.release_id != plan.target_release_id or manifest.version != plan.target_version:
                raise UpdateRejectedError("manifest release identity differs from prepared update")
            if artifact_id != plan.artifact_id:
                raise UpdateRejectedError("artifact_id differs from prepared update")
            if target.fingerprint != plan.target_fingerprint:
                raise UpdateRejectedError("target differs from prepared update")
            if verification.actual_sha256 != plan.artifact_sha256:
                raise UpdateRejectedError("artifact bytes differ from prepared update")
        return verification

    def prepare(
        self,
        *,
        runtime: ApplicationRuntime,
        current_release_id: str,
        manifest: ReleaseManifest,
        artifact_id: str,
        artifact_bytes: bytes,
        target: SupportTarget,
        authenticity: ManifestAuthenticityEvidence,
    ) -> UpdatePlan:
        if not isinstance(runtime, ApplicationRuntime):
            raise TypeError("runtime must be ApplicationRuntime")
        if runtime.state != "RUNNING" or runtime.profile_id is None:
            raise UpdateRejectedError("update preparation requires a RUNNING owned profile runtime")
        current_release = _text(current_release_id, "current_release_id")
        verification = self._verify_artifact(
            plan=None,
            manifest=manifest,
            artifact_id=artifact_id,
            artifact_bytes=bytes(artifact_bytes),
            target=target,
            authenticity=authenticity,
        )
        snapshot = RecoveryManager(runtime.headquarters.store).create_snapshot()
        quit_result = runtime.quit()
        if quit_result.status != "STOPPED":
            raise UpdateRejectedError(
                f"runtime could not reach STOPPED before package mutation: {quit_result.status}"
            )
        plan = UpdatePlan(
            update_id=f"upd_{uuid.uuid4().hex}",
            profile_id=snapshot.profile_id,
            current_release_id=current_release,
            target_release_id=manifest.release_id,
            target_version=manifest.version,
            manifest_fingerprint=manifest.fingerprint,
            artifact_id=artifact_id,
            artifact_sha256=verification.actual_sha256,
            target_fingerprint=target.fingerprint,
            snapshot_sha256=snapshot.sha256,
            snapshot_size_bytes=snapshot.size_bytes,
            snapshot_lineage_schema_version=snapshot.lineage_schema_version,
        )
        self._write_journal(
            _Journal(
                plan=plan,
                state="PREPARED",
                history=[
                    {
                        "state": "PREPARED",
                        "evidence": f"snapshot:{snapshot.sha256}",
                    }
                ],
            ),
            create=True,
        )
        return plan

    def _recover_runtime_for_compensation(self, runtime: ApplicationRuntime) -> bool:
        if runtime.state == "STOPPED":
            return True
        try:
            return runtime.quit().status == "STOPPED"
        except Exception:
            return False

    def _recovery_required(
        self,
        plan: UpdatePlan,
        *,
        expected: set[str],
        reason: str,
        evidence: str,
        install_receipt: PackageActionReceipt | None = None,
        rollback_receipt: PackageActionReceipt | None = None,
    ) -> UpdateResult:
        journal = self._transition(
            plan.update_id,
            expected=expected,
            new_state="RECOVERY_REQUIRED",
            evidence=evidence,
            install_receipt=install_receipt,
            rollback_receipt=rollback_receipt,
            reason=reason,
        )
        return UpdateResult(
            "RECOVERY_REQUIRED",
            plan,
            journal.install_receipt,
            journal.rollback_receipt,
            journal.restored_sha256,
            reason,
        )

    def _compensate(
        self,
        plan: UpdatePlan,
        *,
        driver: PackageDriver,
        starting_state: str,
        install_receipt: PackageActionReceipt,
        reason: str,
    ) -> UpdateResult:
        self._transition(
            plan.update_id,
            expected={starting_state},
            new_state="ROLLING_BACK",
            evidence=f"compensate:{reason}",
            install_receipt=install_receipt,
            reason=reason,
        )
        rollback_request = self._request(plan, "ROLLBACK")
        try:
            rollback_receipt = self._validate_receipt(
                driver.perform(rollback_request, None),
                rollback_request,
            )
        except Exception as exc:
            return self._recovery_required(
                plan,
                expected={"ROLLING_BACK"},
                reason=f"package rollback outcome is ambiguous: {exc}",
                evidence="rollback:exception",
                install_receipt=install_receipt,
            )
        if rollback_receipt.state != "SUCCEEDED":
            return self._recovery_required(
                plan,
                expected={"ROLLING_BACK"},
                reason=f"package rollback did not succeed: {rollback_receipt.state}",
                evidence=f"rollback:{rollback_receipt.evidence_ref}",
                install_receipt=install_receipt,
                rollback_receipt=rollback_receipt,
            )
        self._transition(
            plan.update_id,
            expected={"ROLLING_BACK"},
            new_state="RESTORING",
            evidence=f"rollback:{rollback_receipt.evidence_ref}",
            install_receipt=install_receipt,
            rollback_receipt=rollback_receipt,
        )
        try:
            restored: RestoreResult = RecoveryManager.restore_snapshot(
                self._data_root_for_plan(plan),
                plan.profile_id,
                expected_sha256=plan.snapshot_sha256,
            )
        except Exception as exc:
            return self._recovery_required(
                plan,
                expected={"RESTORING"},
                reason=f"creative-state snapshot restore failed: {exc}",
                evidence="restore:exception",
                install_receipt=install_receipt,
                rollback_receipt=rollback_receipt,
            )
        if restored.installed_sha256 != plan.snapshot_sha256:
            return self._recovery_required(
                plan,
                expected={"RESTORING"},
                reason="restored creative state does not match prepared snapshot",
                evidence="restore:hash-mismatch",
                install_receipt=install_receipt,
                rollback_receipt=rollback_receipt,
            )
        journal = self._transition(
            plan.update_id,
            expected={"RESTORING"},
            new_state="ROLLED_BACK",
            evidence=f"restore:{restored.installed_sha256}",
            install_receipt=install_receipt,
            rollback_receipt=rollback_receipt,
            restored_sha256=restored.installed_sha256,
            reason=reason,
        )
        return UpdateResult(
            "ROLLED_BACK",
            plan,
            journal.install_receipt,
            journal.rollback_receipt,
            journal.restored_sha256,
            reason,
        )

    def _data_root_for_plan(self, plan: UpdatePlan) -> Path:
        journal = self._read_journal(plan.update_id)
        # The journal intentionally stores no arbitrary data-root path. Recovery
        # is anchored by the coordinator's paired runtime data root supplied at apply.
        data_root = getattr(self, "_active_data_root", None)
        if data_root is None:
            raise ApplicationUpdateError("update compensation has no bound data_root")
        return Path(data_root)

    def apply(
        self,
        *,
        plan: UpdatePlan,
        data_root: str | Path,
        manifest: ReleaseManifest,
        artifact_bytes: bytes,
        target: SupportTarget,
        authenticity: ManifestAuthenticityEvidence,
        driver: PackageDriver,
        process: ProcessIdentity,
        probe: ProcessProbe,
    ) -> UpdateResult:
        if not isinstance(plan, UpdatePlan):
            raise TypeError("plan must be UpdatePlan")
        data = Path(data_root)
        if not data.is_absolute():
            raise ApplicationUpdateError("data_root must be absolute")
        if not isinstance(process, ProcessIdentity):
            raise TypeError("process must be ProcessIdentity")
        journal = self._read_journal(plan.update_id)
        if journal.plan != plan:
            raise UpdateRejectedError("supplied update plan differs from durable journal")
        if journal.state != "PREPARED":
            raise UpdateExecuteOnceError(
                f"update is execute-once and durable state is already {journal.state}"
            )
        self._verify_artifact(
            plan=plan,
            manifest=manifest,
            artifact_id=plan.artifact_id,
            artifact_bytes=bytes(artifact_bytes),
            target=target,
            authenticity=authenticity,
        )
        execution_lease_id = f"__update_{plan.update_id}__"
        acquired = self._leases.acquire(execution_lease_id, process, probe)
        if acquired.status in {"HELD_BY_OTHER", "ALREADY_OWNED", "UNCERTAIN"}:
            raise UpdateBusyError(
                f"update execution ownership unavailable: {acquired.status}"
            )
        if acquired.status not in {"ACQUIRED", "REPLACED_STALE"} or acquired.lease is None:
            raise UpdateBusyError(f"unexpected update execution lease state: {acquired.status}")

        self._active_data_root = data
        result: UpdateResult | None = None
        try:
            self._transition(
                plan.update_id,
                expected={"PREPARED"},
                new_state="INSTALLING",
                evidence="install:started",
            )
            install_request = self._request(plan, "INSTALL")
            try:
                install_receipt = self._validate_receipt(
                    driver.perform(install_request, bytes(artifact_bytes)),
                    install_request,
                )
            except Exception as exc:
                result = self._recovery_required(
                    plan,
                    expected={"INSTALLING"},
                    reason=f"package install outcome is ambiguous: {exc}",
                    evidence="install:exception",
                )
                return result

            if install_receipt.state == "UNKNOWN":
                result = self._recovery_required(
                    plan,
                    expected={"INSTALLING"},
                    reason="package install outcome is UNKNOWN; blind retry/rollback is forbidden",
                    evidence=f"install:{install_receipt.evidence_ref}",
                    install_receipt=install_receipt,
                )
                return result
            if install_receipt.state == "FAILED_SAFE":
                terminal = self._transition(
                    plan.update_id,
                    expected={"INSTALLING"},
                    new_state="FAILED_SAFE",
                    evidence=f"install:{install_receipt.evidence_ref}",
                    install_receipt=install_receipt,
                    reason="package driver proved install failed before changing package state",
                )
                result = UpdateResult(
                    "FAILED_SAFE",
                    plan,
                    terminal.install_receipt,
                    None,
                    None,
                    terminal.reason or "safe install failure",
                )
                return result
            if install_receipt.state == "FAILED_CHANGED":
                result = self._compensate(
                    plan,
                    driver=driver,
                    starting_state="INSTALLING",
                    install_receipt=install_receipt,
                    reason="package install failed after changing package state",
                )
                return result

            self._transition(
                plan.update_id,
                expected={"INSTALLING"},
                new_state="VALIDATING",
                evidence=f"install:{install_receipt.evidence_ref}",
                install_receipt=install_receipt,
            )
            validation_runtime = self.runtime_factory(
                data_root=data,
                state_root=self.state_root,
            )
            validation_failure: str | None = None
            try:
                launch = validation_runtime.launch(
                    profile_id=plan.profile_id,
                    process=process,
                    probe=probe,
                )
                if launch.status != "STARTED":
                    validation_failure = f"post-update runtime launch returned {launch.status}"
                else:
                    quick = validation_runtime.headquarters.store._conn.execute(
                        "PRAGMA quick_check"
                    ).fetchone()
                    foreign = validation_runtime.headquarters.store._conn.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchall()
                    if quick is None or str(quick[0]) != "ok" or foreign:
                        validation_failure = "post-update canonical database integrity check failed"
            except Exception as exc:
                validation_failure = f"post-update Headquarters validation failed: {exc}"

            if validation_failure is None:
                quit_validation = validation_runtime.quit()
                if quit_validation.status != "STOPPED":
                    result = self._recovery_required(
                        plan,
                        expected={"VALIDATING"},
                        reason=(
                            "updated Headquarters validated but could not reach STOPPED; "
                            "package/data compensation is unsafe while ownership remains open"
                        ),
                        evidence=f"validation-quit:{quit_validation.status}",
                        install_receipt=install_receipt,
                    )
                    return result
                terminal = self._transition(
                    plan.update_id,
                    expected={"VALIDATING"},
                    new_state="SUCCEEDED",
                    evidence="validation:headquarters-open-integrity-close",
                    install_receipt=install_receipt,
                    reason="package install and canonical Headquarters validation succeeded",
                )
                result = UpdateResult(
                    "SUCCEEDED",
                    plan,
                    terminal.install_receipt,
                    None,
                    None,
                    terminal.reason or "update succeeded",
                )
                return result

            if not self._recover_runtime_for_compensation(validation_runtime):
                result = self._recovery_required(
                    plan,
                    expected={"VALIDATING"},
                    reason=(
                        f"{validation_failure}; validation runtime could not be stopped, "
                        "so automatic rollback/restore is unsafe"
                    ),
                    evidence="validation:runtime-still-owned",
                    install_receipt=install_receipt,
                )
                return result
            result = self._compensate(
                plan,
                driver=driver,
                starting_state="VALIDATING",
                install_receipt=install_receipt,
                reason=validation_failure,
            )
            return result
        finally:
            try:
                del self._active_data_root
            except AttributeError:
                pass
            try:
                self._leases.release(
                    execution_lease_id,
                    process=process,
                    lease_nonce=acquired.lease.lease_nonce,
                )
            except Exception:
                # The update journal is already the final/ambiguous truth source.
                # A stale execution lease may be recovered only through normal
                # verified-dead InstanceLeaseManager semantics on a later process.
                pass

    def status(self, update_id: str) -> UpdateStatus:
        journal = self._read_journal(update_id)
        return UpdateStatus(
            journal.state,
            journal.plan,
            journal.install_receipt,
            journal.rollback_receipt,
            journal.restored_sha256,
            journal.reason,
        )
