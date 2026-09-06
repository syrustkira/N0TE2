#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class SupervisionError(RuntimeError):
    pass


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SupervisionError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SupervisionError(f"{path} must contain a JSON object")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SupervisionError(message)


def inspect_supervision(repo: Path) -> dict:
    repo = Path(repo)
    registry = _load(repo / "governance/automation_registry.json")
    current = _load(repo / "governance/current_state.json")
    lifecycle_policy = _load(repo / "governance/context_lifecycle.json")

    root = registry.get("supervisor")
    _require(root == "N0TE-SUPERVISOR", "supervision root must be N0TE-SUPERVISOR")
    runtime_contract = registry.get("runtime_state_contract", {})
    _require(isinstance(runtime_contract, dict), "automation registry runtime_state_contract must be an object")
    _require(runtime_contract.get("registry_is_runtime_source") is False, "automation registry cannot own live runtime state")
    _require(
        runtime_contract.get("construction_lifecycle_source") == "governance/current_state.json",
        "construction lifecycle source must remain governance/current_state.json",
    )
    actors = registry.get("actors")
    _require(isinstance(actors, list) and actors, "automation registry requires actors")
    ids = [actor.get("id") for actor in actors]
    _require(all(isinstance(item, str) and item for item in ids), "automation actor missing stable id")
    _require(len(ids) == len(set(ids)), "automation actor ids must be unique")

    observed = []
    for actor in actors:
        actor_id = actor["id"]
        _require(actor.get("parent") == root, f"{actor_id} is orphaned from the supervision graph")
        for field in (
            "purpose",
            "reason_running",
            "wake_condition",
            "retirement_condition",
            "allowed_mutations",
            "failure_policy",
            "escalation_target",
            "reports_to",
        ):
            value = actor.get(field)
            _require(value not in (None, "", []), f"{actor_id} lacks {field}")
        _require(actor.get("reports_to") == root, f"{actor_id} does not report to the supervision graph")
        lifecycle = actor.get("lifecycle", {})
        state = lifecycle.get("state")
        _require(state in {"ACTIVE", "DORMANT", "RETIRED", "QUARANTINED"}, f"{actor_id} has invalid lifecycle state")
        _require(lifecycle.get("last_observation") is not None, f"{actor_id} lacks last_observation")
        _require(lifecycle.get("next_wake_condition"), f"{actor_id} lacks next_wake_condition")
        failure = actor.get("failure_policy", {})
        _require(isinstance(failure, dict), f"{actor_id} failure_policy must be an object")
        _require(isinstance(failure.get("max_retries"), int) and failure["max_retries"] >= 0, f"{actor_id} requires bounded max_retries")
        _require(bool(failure.get("terminal_states")), f"{actor_id} requires terminal failure states")
        observability = actor.get("observability", {})
        _require(observability.get("reactivation_is_event") is True, f"{actor_id} may not reactivate silently")
        _require(observability.get("observation_artifact"), f"{actor_id} lacks supervision observation output")
        observed.append(
            {
                "id": actor_id,
                "kind": actor.get("kind"),
                "role_class": actor.get("role_class"),
                "state": state,
                "purpose": actor.get("purpose"),
                "reason_running": actor.get("reason_running"),
                "allowed_mutations": actor.get("allowed_mutations"),
                "wake_condition": actor.get("wake_condition"),
                "next_wake_condition": lifecycle.get("next_wake_condition"),
                "retirement_condition": actor.get("retirement_condition"),
                "last_observation": lifecycle.get("last_observation"),
                "health": lifecycle.get("health"),
                "escalation_target": actor.get("escalation_target"),
            }
        )

    controller = next((actor for actor in actors if actor.get("id") == "AUTO-CONSTRUCTION-CONTROLLER-001"), None)
    _require(controller is not None, "construction controller is not registered")
    _require(
        controller.get("runtime_state_source") == "REPOSITORY_GOVERNANCE_STATE",
        "construction controller runtime source drifted",
    )
    _require(
        controller.get("lifecycle", {}).get("health") == "DERIVED_FROM_CURRENT_STATE",
        "construction controller health must be derived from current_state",
    )
    construction_state = current.get("lifecycle_state")
    build_actor_roles = {"N0TE_BUILD_HARNESS_COORDINATOR", "N0TE_BUILD_HARNESS_EXECUTOR"}
    build_actors = [actor for actor in actors if actor.get("role_class") in build_actor_roles]
    _require(build_actors, "no N0TE build-harness actors are registered")

    if construction_state == "ACTIVE":
        _require(current.get("active_node"), "active construction requires active_node")
        _require(current.get("active_increment"), "active construction requires active_increment")
        _require(
            controller.get("lifecycle", {}).get("state") in {"DORMANT", "ACTIVE"},
            "declarative construction controller cannot be retired or quarantined during active governed work",
        )
    else:
        _require(construction_state in {"STABLE", "WAITING", "BLOCKED"}, "unknown construction lifecycle state")
        _require(current.get("active_node") is None, "terminal construction cannot retain active_node")
        _require(current.get("active_increment") is None, "terminal construction cannot retain active_increment")
        for actor in build_actors:
            _require(
                actor.get("lifecycle", {}).get("state") in {"DORMANT", "RETIRED", "QUARANTINED"},
                f"terminal construction cannot leave build actor ACTIVE: {actor.get('id')}",
            )
            _require(
                actor.get("auto_spawn_successor") is False,
                f"terminal construction cannot let build actor auto-spawn successor work: {actor.get('id')}",
            )

    return {
        "schema_version": 3,
        "supervisor": root,
        "construction": {
            "state": construction_state,
            "active_node": current.get("active_node"),
            "active_increment": current.get("active_increment"),
            "terminal_reason": current.get("terminal_reason"),
            "wake_condition": current.get("wake_condition"),
            "runtime_source": runtime_contract.get("construction_lifecycle_source"),
        },
        "actors": observed,
        "context_lifecycle": {
            "policy_id": lifecycle_policy.get("policy_id"),
            "flattening_rule": lifecycle_policy.get("constitutional_rule"),
            "projection_is_disposable": lifecycle_policy.get("projection_contract", {}).get("projection_is_disposable"),
            "conversation_is_provenance_not_authority": lifecycle_policy.get("conversation_distillation", {}).get("conversation_is_provenance_not_authority"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        snapshot = inspect_supervision(Path(args.repo).resolve())
    except SupervisionError as exc:
        print(f"N0TE2 SUPERVISION: RED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        construction = snapshot["construction"]
        print(
            "N0TE2 SUPERVISION: GREEN "
            f"construction={construction['state']} "
            f"active={construction.get('active_increment') or '-'} "
            f"actors={len(snapshot['actors'])}"
        )
        for actor in snapshot["actors"]:
            print(
                f"- {actor['id']}: {actor['state']} | {actor['reason_running']} | "
                f"next={actor['next_wake_condition']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
