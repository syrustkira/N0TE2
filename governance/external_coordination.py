"""Deterministic external coordination helpers.

This module is construction/control-plane plumbing. It deliberately does not own
N0TE product semantics, artist meaning, creative judgment, or human authority.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping


VALID_EXECUTOR_STATES = {
    "OBSERVED_WORKING",
    "OBSERVED_LIMITED",
    "SUPPORTED_NOT_PROBED",
    "UNAVAILABLE",
    "REQUIRES_AUTHORITY",
    "UNKNOWN",
}


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
