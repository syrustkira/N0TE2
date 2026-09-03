"""Deterministic external coordination helpers.

This module is construction/control-plane plumbing. It deliberately does not own
N0TE product semantics, artist meaning, creative judgment, or human authority.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping, Sequence


VALID_EXECUTOR_STATES = {
    "OBSERVED_WORKING",
    "OBSERVED_LIMITED",
    "SUPPORTED_NOT_PROBED",
    "UNAVAILABLE",
    "REQUIRES_AUTHORITY",
    "UNKNOWN",
}


class CoordinationContractError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeActorState:
    actor_id: str
    semantic_lifecycle: str
    scheduler_enabled: bool | None
    execution_claim: str | None
    health: str
    observed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_digest(value: Any) -> str:
    """Stable digest for change suppression and CONTINUE delta checks."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def compact_continue_snapshot(
    *,
    work_id: str | None,
    state: str,
    evidence_basis: Mapping[str, Any],
    blocker: str | None = None,
    next_action: str | None = None,
    owner: str | None = None,
    requires_artist: bool = False,
) -> dict[str, Any]:
    """Return a compact coordination snapshot without flattening product scope."""
    snapshot = {
        "work_id": work_id,
        "state": state,
        "owner": owner,
        "blocker": blocker,
        "next_action": next_action,
        "requires_artist": bool(requires_artist),
        "evidence_basis": dict(evidence_basis),
    }
    snapshot["change_digest"] = canonical_digest(snapshot)
    return snapshot


def changed(previous_digest: str | None, current_snapshot: Mapping[str, Any]) -> bool:
    current = current_snapshot.get("change_digest") or canonical_digest(dict(current_snapshot))
    return previous_digest != current


def normalize_runtime_actor(
    registry_actor: Mapping[str, Any],
    observed_runtime: Mapping[str, Any] | None,
) -> RuntimeActorState:
    """Separate semantic lifecycle from actual scheduler/runtime liveness."""
    observed_runtime = observed_runtime or {}
    lifecycle = registry_actor.get("lifecycle") or {}
    return RuntimeActorState(
        actor_id=str(registry_actor["id"]),
        semantic_lifecycle=str(lifecycle.get("state", "UNKNOWN")),
        scheduler_enabled=observed_runtime.get("scheduler_enabled"),
        execution_claim=observed_runtime.get("execution_claim"),
        health=str(observed_runtime.get("health", "UNOBSERVED_RUNTIME")),
        observed_at=observed_runtime.get("observed_at"),
    )


def runtime_actor_conflict(actor: RuntimeActorState) -> dict[str, Any] | None:
    """Detect semantic/runtime liveness drift without silently choosing authority."""
    if actor.scheduler_enabled is None:
        return None
    if actor.semantic_lifecycle == "ACTIVE" and actor.scheduler_enabled is False:
        return {
            "actor_id": actor.actor_id,
            "conflict": "SEMANTIC_ACTIVE_RUNTIME_DISABLED",
            "requires_reconciliation": True,
            "observed_at": actor.observed_at,
        }
    if actor.semantic_lifecycle != "ACTIVE" and actor.scheduler_enabled is True:
        return {
            "actor_id": actor.actor_id,
            "conflict": "SEMANTIC_NONACTIVE_RUNTIME_ENABLED",
            "requires_reconciliation": True,
            "observed_at": actor.observed_at,
        }
    return None


def _surface_map(capability_matrix: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(surface["id"]): surface for surface in capability_matrix.get("surfaces", [])}


def operation_state(
    capability_matrix: Mapping[str, Any],
    surface_id: str,
    operation: str,
) -> str:
    surface = _surface_map(capability_matrix).get(surface_id)
    if not surface:
        return "UNKNOWN"
    record = (surface.get("operations") or {}).get(operation)
    if not record:
        return "UNKNOWN"
    state = str(record.get("state", "UNKNOWN"))
    return state if state in VALID_EXECUTOR_STATES else "UNKNOWN"


def select_executor(
    capability_matrix: Mapping[str, Any],
    operation: str,
    preferred_surfaces: Iterable[str],
) -> dict[str, Any]:
    """Select the strongest known executor while preserving truthful degradation."""
    limited: list[str] = []
    authority: list[str] = []
    supported_unprobed: list[str] = []
    for surface_id in preferred_surfaces:
        state = operation_state(capability_matrix, surface_id, operation)
        if state == "OBSERVED_WORKING":
            return {"surface": surface_id, "state": state, "operation": operation}
        if state == "OBSERVED_LIMITED":
            limited.append(surface_id)
        elif state == "REQUIRES_AUTHORITY":
            authority.append(surface_id)
        elif state == "SUPPORTED_NOT_PROBED":
            supported_unprobed.append(surface_id)
    if limited:
        return {"surface": limited[0], "state": "OBSERVED_LIMITED", "operation": operation}
    if authority:
        return {"surface": authority[0], "state": "REQUIRES_AUTHORITY", "operation": operation}
    if supported_unprobed:
        return {"surface": supported_unprobed[0], "state": "SUPPORTED_NOT_PROBED", "operation": operation}
    return {"surface": None, "state": "UNAVAILABLE", "operation": operation}


def _require_fields(record: Mapping[str, Any], fields: Sequence[str], label: str) -> None:
    missing = [field for field in fields if field not in record or record[field] is None]
    if missing:
        raise CoordinationContractError(f"{label} missing required fields: {', '.join(missing)}")


def validate_action_request(contract: Mapping[str, Any], request: Mapping[str, Any]) -> None:
    """Validate a cross-layer action request without granting authority."""
    _require_fields(request, contract.get("required_request_fields", []), "action request")
    if not str(request.get("trace_id", "")).strip():
        raise CoordinationContractError("action request trace_id must be non-empty")
    if not str(request.get("operation_id", "")).strip():
        raise CoordinationContractError("action request operation_id must be non-empty")
    if not str(request.get("idempotency_key", "")).strip():
        raise CoordinationContractError("action request idempotency_key must be non-empty")


def prepare_action_request(
    contract: Mapping[str, Any],
    *,
    operation_id: str,
    trace_id: str,
    requested_by: str,
    semantic_target: str,
    desired_outcome: str,
    executor_class: str,
    authority_basis: Any,
    state_basis: Any,
    preconditions: Sequence[Any],
    idempotency_key: str,
    approval_state: str,
    artifact_refs: Sequence[str],
    expected_effect: str,
    consulted_context_refs: Sequence[str] = (),
    parent_operation_id: str | None = None,
) -> dict[str, Any]:
    """Prepare one deterministic action envelope with memory-consultation provenance."""
    request: dict[str, Any] = {
        "operation_id": operation_id,
        "trace_id": trace_id,
        "requested_by": requested_by,
        "semantic_target": semantic_target,
        "desired_outcome": desired_outcome,
        "executor_class": executor_class,
        "authority_basis": authority_basis,
        "state_basis": state_basis,
        "preconditions": list(preconditions),
        "idempotency_key": idempotency_key,
        "approval_state": approval_state,
        "artifact_refs": list(artifact_refs),
        "expected_effect": expected_effect,
        "consulted_context_refs": list(consulted_context_refs),
    }
    if parent_operation_id:
        request["parent_operation_id"] = parent_operation_id
    validate_action_request(contract, request)
    return request


def validate_action_result(
    contract: Mapping[str, Any],
    request: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    """Validate an executor result and ensure trace identity survived the hop."""
    validate_action_request(contract, request)
    _require_fields(result, contract.get("required_result_fields", []), "action result")
    if result.get("operation_id") != request.get("operation_id"):
        raise CoordinationContractError("action result operation_id does not match request")
    if result.get("trace_id") != request.get("trace_id"):
        raise CoordinationContractError("action result trace_id does not match request")
    if result.get("result_state") not in set(contract.get("result_states", [])):
        raise CoordinationContractError("action result has unsupported result_state")


def build_action_result(
    contract: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    executor: str,
    result_state: str,
    observed_effect: Any,
    evidence_refs: Sequence[str],
    observed_at: str,
    retry_safe: bool,
    reconciliation_required: bool,
    failure_signature: str | None = None,
    recovery_action: str | None = None,
    acceptance_refs: Sequence[str] = (),
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "operation_id": request.get("operation_id"),
        "trace_id": request.get("trace_id"),
        "executor": executor,
        "result_state": result_state,
        "observed_effect": observed_effect,
        "evidence_refs": list(evidence_refs),
        "observed_at": observed_at,
        "retry_safe": bool(retry_safe),
        "reconciliation_required": bool(reconciliation_required),
        "acceptance_refs": list(acceptance_refs),
    }
    if failure_signature:
        result["failure_signature"] = failure_signature
    if recovery_action:
        result["recovery_action"] = recovery_action
    validate_action_result(contract, request, result)
    return result


def acceptance_evidence_status(
    spine: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Report evidence state without inferring later acceptance from adjacent proof."""
    state_fields = {
        "MAPPED": ("requirement_id", "canonical_scope_ref"),
        "IMPLEMENTED": ("implementation_refs",),
        "INTEGRATED": ("integration_refs",),
        "REACHABLE": ("user_reachability_refs",),
        "VERIFIED": ("verification_refs",),
        "RECOVERABLE": ("failure_recovery_refs",),
        "AUTHORITY_SAFE": ("authority_security_refs",),
        "CONSUMER_ACCEPTED": ("consumer_acceptance_refs",),
        "VALUE_EVIDENCED": ("value_evidence_refs",),
    }
    declared_states = list(spine.get("states", []))
    status: dict[str, bool] = {}
    highest_contiguous: str | None = None
    contiguous = True
    for state in declared_states:
        fields = state_fields.get(state, ())
        proven = bool(fields) and all(bool(evidence.get(field)) for field in fields)
        status[state] = proven
        if contiguous and proven:
            highest_contiguous = state
        else:
            contiguous = False
    return {
        "states": status,
        "highest_contiguous_state": highest_contiguous,
        "evidence_digest": canonical_digest(dict(evidence)),
    }
