from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .operations import OperationJournal, OperationRecord

TRANSACTION_STATUSES = {"COMPLETE", "COMPENSATED", "RECOVERY_REQUIRED", "UNKNOWN"}
STEP_EXECUTION_STATUSES = {"SUCCEEDED", "FAILED", "UNKNOWN"}
CHANGE_STATES = {"APPLIED", "NOT_APPLIED", "UNKNOWN"}
POSTCONDITION_STATUSES = {"SATISFIED", "FAILED", "UNKNOWN"}
COMPENSATION_STATUSES = {"RESTORED", "FAILED", "UNKNOWN"}


class TransactionError(RuntimeError):
    """Invalid transaction plan or transaction lifecycle."""


def _text(value: str, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise TransactionError(f"{field} must not be empty")
    return text


def _optional_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _bool(value: bool, field: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field} must be a real bool")
    return value


def _enum(value: str, allowed: set[str], field: str) -> str:
    text = str(value).strip().upper()
    if text not in allowed:
        raise TransactionError(f"unsupported {field}: {text}")
    return text


@dataclass(frozen=True)
class TransactionStep:
    step_id: str
    description: str
    postcondition_ref: str
    compensatable: bool
    depends_on_step_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _text(self.step_id, "step.step_id"))
        object.__setattr__(self, "description", _text(self.description, "step.description"))
        object.__setattr__(self, "postcondition_ref", _text(self.postcondition_ref, "step.postcondition_ref"))
        object.__setattr__(self, "compensatable", _bool(self.compensatable, "step.compensatable"))
        deps = tuple(_text(value, "step.depends_on_step_ids") for value in self.depends_on_step_ids)
        if len(deps) != len(set(deps)):
            raise TransactionError("step dependencies must be unique")
        if self.step_id in deps:
            raise TransactionError("step cannot depend on itself")
        object.__setattr__(self, "depends_on_step_ids", deps)


@dataclass(frozen=True)
class TransactionPlan:
    transaction_id: str
    operation_id: str
    steps: tuple[TransactionStep, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "transaction_id", _text(self.transaction_id, "plan.transaction_id"))
        object.__setattr__(self, "operation_id", _text(self.operation_id, "plan.operation_id"))
        steps = tuple(self.steps)
        if not steps:
            raise TransactionError("transaction plan requires at least one step")
        if not all(isinstance(step, TransactionStep) for step in steps):
            raise TypeError("steps must contain TransactionStep values")
        ids = tuple(step.step_id for step in steps)
        if len(ids) != len(set(ids)):
            raise TransactionError("transaction step IDs must be unique")
        seen: set[str] = set()
        for step in steps:
            missing = [dep for dep in step.depends_on_step_ids if dep not in seen]
            if missing:
                raise TransactionError(
                    f"step {step.step_id} has unknown or forward dependencies: {missing}"
                )
            seen.add(step.step_id)
        object.__setattr__(self, "steps", steps)


@dataclass(frozen=True)
class TransactionSnapshot:
    transaction_id: str
    operation_id: str
    snapshot_ref: str
    snapshot_fingerprint: str
    evidence_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "transaction_id", _text(self.transaction_id, "snapshot.transaction_id"))
        object.__setattr__(self, "operation_id", _text(self.operation_id, "snapshot.operation_id"))
        object.__setattr__(self, "snapshot_ref", _text(self.snapshot_ref, "snapshot.snapshot_ref"))
        object.__setattr__(self, "snapshot_fingerprint", _text(self.snapshot_fingerprint, "snapshot.snapshot_fingerprint"))
        object.__setattr__(self, "evidence_ref", _text(self.evidence_ref, "snapshot.evidence_ref"))


@dataclass(frozen=True)
class StepExecution:
    step_id: str
    status: str
    change_state: str
    evidence_ref: str
    result_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _text(self.step_id, "execution.step_id"))
        object.__setattr__(self, "status", _enum(self.status, STEP_EXECUTION_STATUSES, "step execution status"))
        object.__setattr__(self, "change_state", _enum(self.change_state, CHANGE_STATES, "step change state"))
        object.__setattr__(self, "evidence_ref", _text(self.evidence_ref, "execution.evidence_ref"))
        object.__setattr__(self, "result_fingerprint", _optional_text(self.result_fingerprint, "execution.result_fingerprint"))
        if self.status == "SUCCEEDED" and self.change_state == "UNKNOWN":
            raise TransactionError("successful execution cannot have UNKNOWN change state")


@dataclass(frozen=True)
class PostconditionResult:
    step_id: str
    postcondition_ref: str
    status: str
    evidence_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _text(self.step_id, "postcondition.step_id"))
        object.__setattr__(self, "postcondition_ref", _text(self.postcondition_ref, "postcondition.postcondition_ref"))
        object.__setattr__(self, "status", _enum(self.status, POSTCONDITION_STATUSES, "postcondition status"))
        object.__setattr__(self, "evidence_ref", _text(self.evidence_ref, "postcondition.evidence_ref"))


@dataclass(frozen=True)
class CompensationResult:
    step_id: str
    snapshot_ref: str
    status: str
    evidence_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _text(self.step_id, "compensation.step_id"))
        object.__setattr__(self, "snapshot_ref", _text(self.snapshot_ref, "compensation.snapshot_ref"))
        object.__setattr__(self, "status", _enum(self.status, COMPENSATION_STATUSES, "compensation status"))
        object.__setattr__(self, "evidence_ref", _text(self.evidence_ref, "compensation.evidence_ref"))


@dataclass(frozen=True)
class TransactionReceipt:
    transaction_id: str
    operation_id: str
    snapshot_ref: str
    receipt_ref: str
    evidence_ref: str
    result_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "transaction_id", _text(self.transaction_id, "receipt.transaction_id"))
        object.__setattr__(self, "operation_id", _text(self.operation_id, "receipt.operation_id"))
        object.__setattr__(self, "snapshot_ref", _text(self.snapshot_ref, "receipt.snapshot_ref"))
        object.__setattr__(self, "receipt_ref", _text(self.receipt_ref, "receipt.receipt_ref"))
        object.__setattr__(self, "evidence_ref", _text(self.evidence_ref, "receipt.evidence_ref"))
        object.__setattr__(self, "result_fingerprint", _text(self.result_fingerprint, "receipt.result_fingerprint"))


@dataclass(frozen=True)
class TransactionResult:
    status: str
    transaction_id: str
    operation_id: str
    snapshot_ref: str | None
    executed_step_ids: tuple[str, ...]
    compensated_step_ids: tuple[str, ...]
    failed_step_id: str | None
    evidence_refs: tuple[str, ...]
    operation: OperationRecord

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _enum(self.status, TRANSACTION_STATUSES, "transaction status"))
        object.__setattr__(self, "transaction_id", _text(self.transaction_id, "result.transaction_id"))
        object.__setattr__(self, "operation_id", _text(self.operation_id, "result.operation_id"))
        object.__setattr__(self, "snapshot_ref", _optional_text(self.snapshot_ref, "result.snapshot_ref"))
        object.__setattr__(self, "executed_step_ids", tuple(self.executed_step_ids))
        object.__setattr__(self, "compensated_step_ids", tuple(self.compensated_step_ids))
        object.__setattr__(self, "failed_step_id", _optional_text(self.failed_step_id, "result.failed_step_id"))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


class TransactionDriver(Protocol):
    def prepare_snapshot(self, plan: TransactionPlan) -> TransactionSnapshot: ...
    def execute_step(self, step: TransactionStep) -> StepExecution: ...
    def verify_postcondition(self, step: TransactionStep, execution: StepExecution) -> PostconditionResult: ...
    def compensate_step(self, step: TransactionStep, snapshot: TransactionSnapshot) -> CompensationResult: ...
    def success_receipt(self, plan: TransactionPlan, snapshot: TransactionSnapshot) -> TransactionReceipt: ...


class TransactionCoordinator:
    """Host/provider-neutral bounded transaction coordinator over one claimed operation."""

    _DRIVER_METHODS = (
        "prepare_snapshot",
        "execute_step",
        "verify_postcondition",
        "compensate_step",
        "success_receipt",
    )

    def __init__(self, journal: OperationJournal):
        if not isinstance(journal, OperationJournal):
            raise TypeError("TransactionCoordinator requires OperationJournal")
        self.journal = journal

    @staticmethod
    def _driver_methods(driver: object) -> dict[str, object]:
        methods: dict[str, object] = {}
        for name in TransactionCoordinator._DRIVER_METHODS:
            method = getattr(driver, name, None)
            if not callable(method):
                raise TransactionError(f"transaction driver must implement {name}()")
            methods[name] = method
        return methods

    @staticmethod
    def _exception_ref(plan: TransactionPlan, phase: str, step_id: str | None, exc: Exception) -> str:
        suffix = "none" if step_id is None else step_id
        return f"transaction:{plan.transaction_id}:{phase}:{suffix}:exception:{type(exc).__name__}"

    @staticmethod
    def _require_snapshot_identity(plan: TransactionPlan, snapshot: TransactionSnapshot) -> None:
        if snapshot.transaction_id != plan.transaction_id or snapshot.operation_id != plan.operation_id:
            raise TransactionError("recovery snapshot belongs to a different transaction/operation")

    @staticmethod
    def _require_execution_identity(step: TransactionStep, execution: StepExecution) -> None:
        if execution.step_id != step.step_id:
            raise TransactionError("step execution belongs to a different step")

    @staticmethod
    def _require_postcondition_identity(step: TransactionStep, post: PostconditionResult) -> None:
        if post.step_id != step.step_id or post.postcondition_ref != step.postcondition_ref:
            raise TransactionError("postcondition result belongs to a different step/condition")

    @staticmethod
    def _require_compensation_identity(
        step: TransactionStep,
        snapshot: TransactionSnapshot,
        result: CompensationResult,
    ) -> None:
        if result.step_id != step.step_id or result.snapshot_ref != snapshot.snapshot_ref:
            raise TransactionError("compensation result belongs to a different step/snapshot")

    @staticmethod
    def _require_receipt_identity(
        plan: TransactionPlan,
        snapshot: TransactionSnapshot,
        receipt: TransactionReceipt,
    ) -> None:
        if (
            receipt.transaction_id != plan.transaction_id
            or receipt.operation_id != plan.operation_id
            or receipt.snapshot_ref != snapshot.snapshot_ref
        ):
            raise TransactionError("transaction receipt belongs to a different transaction/operation/snapshot")

    def _safe_failure(
        self,
        plan: TransactionPlan,
        evidence_ref: str,
    ) -> TransactionResult:
        operation = self.journal.complete_failure(plan.operation_id, evidence_ref=evidence_ref)
        return TransactionResult(
            "COMPENSATED",
            plan.transaction_id,
            plan.operation_id,
            None,
            (),
            (),
            None,
            (evidence_ref,),
            operation,
        )

    def _compensate(
        self,
        plan: TransactionPlan,
        snapshot: TransactionSnapshot,
        compensate: object,
        changed_steps: list[TransactionStep],
        evidence: list[str],
        *,
        root_unknown: bool,
        failed_step_id: str | None,
    ) -> TransactionResult:
        root_failure_ref = evidence[-1]
        recovery_evidence_ref = root_failure_ref if root_unknown else None
        restored: list[str] = []
        recovery_required = root_unknown

        for step in reversed(changed_steps):
            if not step.compensatable:
                recovery_required = True
                ref = f"transaction:{plan.transaction_id}:noncompensatable:{step.step_id}"
                evidence.append(ref)
                recovery_evidence_ref = ref
                continue
            try:
                result = compensate(step, snapshot)  # type: ignore[misc]
                if not isinstance(result, CompensationResult):
                    raise TypeError("compensate_step must return CompensationResult")
                self._require_compensation_identity(step, snapshot, result)
                evidence.append(result.evidence_ref)
                if result.status == "RESTORED":
                    restored.append(step.step_id)
                else:
                    recovery_required = True
                    recovery_evidence_ref = result.evidence_ref
            except Exception as exc:
                recovery_required = True
                ref = self._exception_ref(plan, "compensate", step.step_id, exc)
                evidence.append(ref)
                recovery_evidence_ref = ref

        if recovery_required:
            operation = self.journal.mark_unknown(
                plan.operation_id,
                evidence_ref=recovery_evidence_ref or root_failure_ref,
            )
            return TransactionResult(
                "RECOVERY_REQUIRED",
                plan.transaction_id,
                plan.operation_id,
                snapshot.snapshot_ref,
                tuple(step.step_id for step in changed_steps),
                tuple(restored),
                failed_step_id,
                tuple(evidence),
                operation,
            )

        operation = self.journal.complete_failure(
            plan.operation_id,
            evidence_ref=root_failure_ref,
            result_fingerprint=f"compensated:{snapshot.snapshot_fingerprint}",
        )
        return TransactionResult(
            "COMPENSATED",
            plan.transaction_id,
            plan.operation_id,
            snapshot.snapshot_ref,
            tuple(step.step_id for step in changed_steps),
            tuple(restored),
            failed_step_id,
            tuple(evidence),
            operation,
        )

    def run(self, plan: TransactionPlan, driver: TransactionDriver) -> TransactionResult:
        if not isinstance(plan, TransactionPlan):
            raise TypeError("plan must be TransactionPlan")
        operation = self.journal.get(plan.operation_id)
        if operation.recorded_state != "EXECUTING":
            raise TransactionError(
                f"transaction requires already-claimed EXECUTING operation, got {operation.recorded_state}"
            )

        try:
            methods = self._driver_methods(driver)
        except Exception as exc:
            return self._safe_failure(
                plan,
                self._exception_ref(plan, "driver-contract", None, exc),
            )

        try:
            snapshot = methods["prepare_snapshot"](plan)  # type: ignore[operator]
            if not isinstance(snapshot, TransactionSnapshot):
                raise TypeError("prepare_snapshot must return TransactionSnapshot")
            self._require_snapshot_identity(plan, snapshot)
        except Exception as exc:
            return self._safe_failure(
                plan,
                self._exception_ref(plan, "snapshot", None, exc),
            )

        evidence: list[str] = [snapshot.evidence_ref]
        changed: list[TransactionStep] = []

        for step in plan.steps:
            try:
                execution = methods["execute_step"](step)  # type: ignore[operator]
                if not isinstance(execution, StepExecution):
                    raise TypeError("execute_step must return StepExecution")
                self._require_execution_identity(step, execution)
                evidence.append(execution.evidence_ref)
            except Exception as exc:
                evidence.append(self._exception_ref(plan, "execute", step.step_id, exc))
                return self._compensate(
                    plan,
                    snapshot,
                    methods["compensate_step"],
                    changed,
                    evidence,
                    root_unknown=True,
                    failed_step_id=step.step_id,
                )

            if execution.change_state == "APPLIED":
                changed.append(step)
            if execution.change_state == "UNKNOWN":
                return self._compensate(
                    plan,
                    snapshot,
                    methods["compensate_step"],
                    changed,
                    evidence,
                    root_unknown=True,
                    failed_step_id=step.step_id,
                )
            if execution.status != "SUCCEEDED":
                return self._compensate(
                    plan,
                    snapshot,
                    methods["compensate_step"],
                    changed,
                    evidence,
                    root_unknown=execution.status == "UNKNOWN",
                    failed_step_id=step.step_id,
                )

            try:
                post = methods["verify_postcondition"](step, execution)  # type: ignore[operator]
                if not isinstance(post, PostconditionResult):
                    raise TypeError("verify_postcondition must return PostconditionResult")
                self._require_postcondition_identity(step, post)
                evidence.append(post.evidence_ref)
            except Exception as exc:
                evidence.append(self._exception_ref(plan, "verify", step.step_id, exc))
                return self._compensate(
                    plan,
                    snapshot,
                    methods["compensate_step"],
                    changed,
                    evidence,
                    root_unknown=True,
                    failed_step_id=step.step_id,
                )
            if post.status != "SATISFIED":
                return self._compensate(
                    plan,
                    snapshot,
                    methods["compensate_step"],
                    changed,
                    evidence,
                    root_unknown=post.status == "UNKNOWN",
                    failed_step_id=step.step_id,
                )

        try:
            receipt = methods["success_receipt"](plan, snapshot)  # type: ignore[operator]
            if not isinstance(receipt, TransactionReceipt):
                raise TypeError("success_receipt must return TransactionReceipt")
            self._require_receipt_identity(plan, snapshot, receipt)
        except Exception as exc:
            evidence.append(self._exception_ref(plan, "receipt", None, exc))
            return self._compensate(
                plan,
                snapshot,
                methods["compensate_step"],
                changed,
                evidence,
                root_unknown=True,
                failed_step_id=None,
            )

        evidence.append(receipt.evidence_ref)
        operation = self.journal.complete_success(
            plan.operation_id,
            receipt_ref=receipt.receipt_ref,
            evidence_ref=receipt.evidence_ref,
            result_fingerprint=receipt.result_fingerprint,
        )
        return TransactionResult(
            "COMPLETE",
            plan.transaction_id,
            plan.operation_id,
            snapshot.snapshot_ref,
            tuple(step.step_id for step in changed),
            (),
            None,
            tuple(evidence),
            operation,
        )
