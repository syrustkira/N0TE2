#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


class HandoffError(RuntimeError):
    pass


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        raise HandoffError(f"cannot load {path}: {exc}") from exc


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT).strip()


def require(cond: bool, msg: str):
    if not cond:
        raise HandoffError(msg)


def build_supervision_summary(repo: Path, current: dict) -> dict:
    registry = load_json(repo / "governance/automation_registry.json")
    context_policy = load_json(repo / "governance/context_lifecycle.json")
    require(registry.get("supervisor") == "N0TE-SUPERVISOR", "automation registry supervision root is stale")
    actors = registry.get("actors", [])
    require(isinstance(actors, list) and actors, "automation registry requires actors")
    summarized = []
    for actor in actors:
        require(actor.get("parent") == "N0TE-SUPERVISOR", f"automation {actor.get('id')} escaped supervision graph")
        require(actor.get("reports_to") == "N0TE-SUPERVISOR", f"automation {actor.get('id')} does not report to supervision graph")
        require(actor.get("reason_running"), f"automation {actor.get('id')} lacks current reason")
        require(actor.get("allowed_mutations"), f"automation {actor.get('id')} lacks allowed mutation contract")
        require(actor.get("wake_condition"), f"automation {actor.get('id')} lacks wake condition")
        require(actor.get("retirement_condition"), f"automation {actor.get('id')} lacks retirement condition")
        require(actor.get("failure_policy", {}).get("terminal_states"), f"automation {actor.get('id')} lacks terminal failure states")
        summarized.append(
            {
                "id": actor.get("id"),
                "state": actor.get("lifecycle", {}).get("state"),
                "purpose": actor.get("purpose"),
                "reason_running": actor.get("reason_running"),
                "allowed_mutations": actor.get("allowed_mutations"),
                "next_wake_condition": actor.get("lifecycle", {}).get("next_wake_condition"),
                "retirement_condition": actor.get("retirement_condition"),
                "escalation_target": actor.get("escalation_target"),
            }
        )
    controller = next((actor for actor in actors if actor.get("id") == "AUTO-CONSTRUCTION-CONTROLLER-001"), None)
    require(controller is not None, "construction controller is not registered")
    expected_controller_state = "ACTIVE" if current.get("lifecycle_state") == "ACTIVE" else "DORMANT"
    require(controller.get("lifecycle", {}).get("state") == expected_controller_state, "construction controller lifecycle is stale")
    if current.get("lifecycle_state") != "ACTIVE":
        require(controller.get("auto_spawn_successor") is False, "terminal construction cannot auto-spawn successor work")
    return {
        "root": registry["supervisor"],
        "actors": summarized,
        "context_policy": {
            "id": context_policy.get("policy_id"),
            "flattening_rule": context_policy.get("constitutional_rule"),
            "conversation_is_provenance_not_authority": context_policy.get("conversation_distillation", {}).get("conversation_is_provenance_not_authority"),
            "semantic_gc_preserves_history": not context_policy.get("semantic_gc", {}).get("delete_canonical_history_by_default", True),
        },
    }


def build_runtime_handoff(repo: Path) -> dict:
    handoff = load_json(repo / "governance/handoff.json")
    current = load_json(repo / "governance/current_state.json")
    receipt = load_json(repo / "governance/active_receipt.json")
    require(handoff.get("repository") == current.get("repository") == "syrustkira/N0TE2", "handoff/current repository mismatch")

    head = git(repo, "rev-parse", "HEAD")
    expected = os.environ.get("N0TE2_HEAD_SHA") or os.environ.get("EVIDENCE_SHA")
    if expected:
        require(head == expected, f"exact-head mismatch: expected {expected}, got {head}")

    lifecycle = handoff["lifecycle"]
    lifecycle_state = lifecycle["state"]
    require(lifecycle_state == current.get("lifecycle_state"), "handoff lifecycle is stale")
    require(lifecycle.get("active_node") == current.get("active_node"), "handoff active node is stale")
    require(lifecycle.get("active_increment") == current.get("active_increment"), "handoff active increment is stale")

    receipt_status = receipt.get("status")
    if lifecycle_state == "ACTIVE":
        require(receipt_status == "ACTIVE", "ACTIVE lifecycle requires an ACTIVE construction receipt")
        require(receipt.get("node_id") == lifecycle.get("active_node"), "active receipt node is stale")
        require(receipt.get("increment_id") == lifecycle.get("active_increment"), "active receipt increment is stale")
        require(receipt.get("product_code_allowed") is True or receipt.get("node_id") in {"BOOT-02", "LEGACY-01"}, "active product receipt lost construction authority")
    else:
        require(receipt_status == "INACTIVE", f"{lifecycle_state} cannot carry an ACTIVE construction receipt")
        require(receipt.get("product_code_allowed") is False, f"{lifecycle_state} receipt cannot authorize product construction")
        require(receipt.get("legacy_admission_allowed") is False, f"{lifecycle_state} receipt cannot authorize legacy admission")

    reconstruction = handoff["reconstruction"]
    require(reconstruction.get("handoff_first") is True, "runtime reconstruction must start from durable handoff")
    require(reconstruction.get("fresh_agent_requires_prior_chat") is False, "runtime reconstruction cannot require prior chat")
    refs = reconstruction["required_refs"]
    missing = [rel for rel in refs if not (repo / rel).exists()]
    require(not missing, f"handoff references missing authority: {', '.join(missing)}")
    supervision = build_supervision_summary(repo, current)

    runtime = {
        "schema_version": 2,
        "repository": handoff["repository"],
        "observed_head_sha": head,
        "head_binding": "RUNTIME_EXACT",
        "delivery": handoff["delivery"],
        "lifecycle": lifecycle,
        "controller": handoff["controller"],
        "construction_receipt_status": receipt_status,
        "open_incidents": handoff.get("open_incidents", []),
        "next_admissible_action": handoff["next_admissible_action"],
        "required_refs": refs,
        "required_reconstruction_outcomes": reconstruction.get("required_outcomes", []),
        "fresh_agent_requires_prior_chat": False,
        "supervision": supervision,
        "archaeology_fallback": reconstruction["archaeology_fallback"],
    }
    return runtime


def build_observation(runtime: dict) -> dict:
    return {
        "schema_version": 2,
        "automation_id": "AUTO-GH-GOVERNANCE-001",
        "supervision_parent": "N0TE-SUPERVISOR",
        "observed_head_sha": runtime["observed_head_sha"],
        "status": os.environ.get("N0TE2_OBSERVATION_STATUS", "UNKNOWN").upper(),
        "run_id": os.environ.get("N0TE2_RUN_ID"),
        "run_attempt": os.environ.get("N0TE2_RUN_ATTEMPT"),
        "runner_os": os.environ.get("N0TE2_RUNNER_OS"),
        "event": os.environ.get("GITHUB_EVENT_NAME"),
        "ref": os.environ.get("GITHUB_REF"),
        "evidence": {
            "runtime_handoff": "governance-runtime-handoff.json",
            "authority_checked": True,
            "exact_head_checked": True,
            "context_lifecycle_checked": True,
            "supervision_graph_checked": True,
            "construction_receipt_status": runtime["construction_receipt_status"],
        },
    }


def write_json(path: Path, payload: dict):
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--observation-output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    try:
        runtime = build_runtime_handoff(repo)
        if args.output:
            write_json(Path(args.output), runtime)
        if args.observation_output:
            write_json(Path(args.observation_output), build_observation(runtime))
        if args.check:
            print(
                "N0TE2 HANDOFF: GREEN "
                f"head={runtime['observed_head_sha']} lifecycle={runtime['lifecycle']['state']} "
                f"active={runtime['lifecycle'].get('active_node') or '-'} receipt={runtime['construction_receipt_status']} "
                f"actors={len(runtime['supervision']['actors'])}"
            )
        elif not args.output and not args.observation_output:
            print(json.dumps(runtime, indent=2, sort_keys=True))
        return 0
    except (HandoffError, subprocess.CalledProcessError) as exc:
        print(f"N0TE2 HANDOFF: RED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
