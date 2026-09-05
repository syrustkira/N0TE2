#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

INCIDENT_REPAIR_NODE = "INCIDENT-REPAIR"
INCIDENT_REPAIR_KIND = "INCIDENT_REPAIR"


class HandoffError(RuntimeError):
    pass


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        raise HandoffError(f"cannot load {path}: {exc}") from exc


def load_jsonl(path: Path):
    rows = []
    try:
        for lineno, raw in enumerate(path.read_text().splitlines(), 1):
            if not raw.strip():
                continue
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError(f"line {lineno} is not an object")
            rows.append(row)
    except Exception as exc:
        raise HandoffError(f"cannot load {path}: {exc}") from exc
    return rows


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT).strip()


def require(cond: bool, msg: str):
    if not cond:
        raise HandoffError(msg)


def _step_outcome(name: str) -> dict:
    raw = str(os.environ.get(name, "")).strip().lower()
    ran = raw in {"success", "failure", "cancelled"}
    return {"ran": ran, "outcome": raw or "unreported", "passed": raw == "success"}


def build_supervision_summary(repo: Path, current: dict) -> dict:
    registry = load_json(repo / "governance/automation_registry.json")
    context_policy = load_json(repo / "governance/context_lifecycle.json")
    require(registry.get("supervisor") == "N0TE-SUPERVISOR", "automation registry supervision root is stale")
    runtime_contract = registry.get("runtime_state_contract")
    require(isinstance(runtime_contract, dict), "automation registry lacks runtime-state ownership contract")
    require(runtime_contract.get("registry_is_runtime_source") is False, "automation registry cannot own live runtime state")
    require(runtime_contract.get("construction_lifecycle_source") == "governance/current_state.json", "construction lifecycle must come from current_state")
    require(runtime_contract.get("external_liveness_requires_runtime_observation") is True, "external liveness must require runtime observation")

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
                "role_class": actor.get("role_class"),
                "declared_state": actor.get("lifecycle", {}).get("state"),
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
    require(controller.get("runtime_state_source") == "REPOSITORY_GOVERNANCE_STATE", "construction controller lost repository governance state binding")
    require(controller.get("lifecycle", {}).get("health") == "DERIVED_FROM_CURRENT_STATE", "construction controller lifecycle must remain derived from current_state")

    if current.get("lifecycle_state") != "ACTIVE":
        build_roles = {"N0TE_BUILD_HARNESS_COORDINATOR", "N0TE_BUILD_HARNESS_EXECUTOR"}
        for actor in actors:
            if actor.get("role_class") in build_roles:
                require(
                    actor.get("lifecycle", {}).get("state") in {"DORMANT", "RETIRED", "QUARANTINED"},
                    f"terminal construction left build actor active: {actor.get('id')}",
                )
                require(actor.get("auto_spawn_successor") is False, f"terminal construction build actor may not auto-spawn: {actor.get('id')}")

    remembrance = context_policy.get("remembrance_contract", {})
    retention = context_policy.get("retention_contract", {})
    consultation = context_policy.get("consultation_contract", {})
    require(remembrance.get("never_require_user_repetition_when_retrievable") is True, "remembrance lost no-repeat continuity")
    require(retention.get("canonical_history_retained_by_default") is True, "retention lost canonical history")
    require(consultation.get("rule") == "Consultation informs judgment; it never silently grants execution authority.", "consultation authority boundary drifted")

    return {
        "root": registry["supervisor"],
        "runtime_state_contract": runtime_contract,
        "construction_lifecycle": {
            "source": "governance/current_state.json",
            "state": current.get("lifecycle_state"),
            "active_node": current.get("active_node"),
            "active_increment": current.get("active_increment"),
        },
        "actors": summarized,
        "context_policy": {
            "id": context_policy.get("policy_id"),
            "flattening_rule": context_policy.get("constitutional_rule"),
            "conversation_is_provenance_not_authority": context_policy.get("conversation_distillation", {}).get("conversation_is_provenance_not_authority"),
            "semantic_gc_preserves_history": not context_policy.get("semantic_gc", {}).get("delete_canonical_history_by_default", True),
            "remembrance_retrieves_instead_of_reasking": remembrance.get("never_require_user_repetition_when_retrievable"),
            "retention_preserves_canonical_history": retention.get("canonical_history_retained_by_default"),
            "foreground_focus_preserves_retained_scope": retention.get("foreground_focus_never_deletes_retained_scope"),
            "consultation_precedence": consultation.get("precedence", []),
            "consultation_rule": consultation.get("rule"),
        },
    }


def build_runtime_handoff(repo: Path) -> dict:
    handoff = load_json(repo / "governance/handoff.json")
    current = load_json(repo / "governance/current_state.json")
    requirements = load_json(repo / "governance/requirements.json")
    incidents = load_jsonl(repo / "governance/incidents.jsonl")
    receipt = load_json(repo / "governance/active_receipt.json")
    action_contract = load_json(repo / "governance/action_receipt_contract.json")
    acceptance_spine = load_json(repo / "governance/acceptance_evidence_spine.json")
    require(handoff.get("repository") == current.get("repository") == "syrustkira/N0TE2", "handoff/current repository mismatch")
    require(action_contract.get("contract_id") == "ACTION-RECEIPT-001", "action/receipt contract identity drifted")
    require(acceptance_spine.get("spine_id") == "ACCEPTANCE-EVIDENCE-001", "acceptance evidence spine identity drifted")

    head = git(repo, "rev-parse", "HEAD")
    expected = os.environ.get("N0TE2_HEAD_SHA") or os.environ.get("EVIDENCE_SHA")
    if expected:
        require(head == expected, f"exact-head mismatch: expected {expected}, got {head}")

    lifecycle = {
        "state": current.get("lifecycle_state"),
        "active_node": current.get("active_node"),
        "active_increment": current.get("active_increment"),
    }
    lifecycle_state = lifecycle["state"]
    require(lifecycle_state in {"ACTIVE", "STABLE", "WAITING", "BLOCKED"}, "current lifecycle state is invalid")
    if lifecycle_state == "ACTIVE":
        lifecycle["mode"] = "INCIDENT_REPAIR" if lifecycle.get("active_node") == INCIDENT_REPAIR_NODE else "CONSTRUCTION"
    else:
        lifecycle["mode"] = "TERMINAL"

    legacy_lifecycle = handoff.get("lifecycle", {})
    require(isinstance(legacy_lifecycle, dict), "handoff lifecycle compatibility hook must be an object")
    for key, label in (("state", "lifecycle"), ("active_node", "active node"), ("active_increment", "active increment")):
        if key in legacy_lifecycle:
            require(legacy_lifecycle.get(key) == lifecycle.get(key), f"handoff {label} is stale")

    canonical = requirements.get("canonical_scope", {})
    extensions = requirements.get("canonical_extensions", [])
    require(canonical.get("retained_requirement_count"), "canonical retained scope count missing")
    canonical_scope = {
        "source": canonical.get("source"),
        "range": f"REQ-SCOPE-{int(canonical.get('start')):03d}..REQ-SCOPE-{int(canonical.get('end')):03d}",
        "retained_requirement_count": canonical.get("retained_requirement_count"),
        "build_graph_is_product_scope_owner": False,
        "canonical_extensions_unselected": [row.get("id") for row in extensions if row.get("selected") is False],
    }
    open_incidents = [
        row.get("id")
        for row in incidents
        if str(row.get("status", "")).upper().startswith("OPEN") and row.get("id")
    ]
    next_admissible_action = current.get("next_admissible_action")
    require(next_admissible_action, "current state lacks next_admissible_action")

    receipt_status = receipt.get("status")
    repair_summary = None
    if lifecycle_state == "ACTIVE":
        require(receipt_status == "ACTIVE", "ACTIVE lifecycle requires an ACTIVE construction or repair receipt")
        require(receipt.get("node_id") == lifecycle.get("active_node"), "active receipt node is stale")
        require(receipt.get("increment_id") == lifecycle.get("active_increment"), "active receipt increment is stale")
        if lifecycle.get("active_node") == INCIDENT_REPAIR_NODE:
            require(receipt.get("repair_kind") == INCIDENT_REPAIR_KIND, "INCIDENT-REPAIR lifecycle requires repair_kind=INCIDENT_REPAIR")
            incident_ids = receipt.get("incident_repair_ids")
            require(isinstance(incident_ids, list) and incident_ids, "INCIDENT-REPAIR lifecycle requires incident_repair_ids")
            require(receipt.get("product_code_allowed") == current.get("product_code_authorized"), "incident repair product authority is stale")
            repair_summary = {
                "kind": receipt.get("repair_kind"),
                "target_kind": receipt.get("repair_target_kind"),
                "repair_issue": receipt.get("repair_issue"),
                "incident_repair_ids": incident_ids,
                "repair_target_merge_sha": receipt.get("repair_target_merge_sha"),
            }
        else:
            require(receipt.get("product_code_allowed") is True or receipt.get("node_id") in {"BOOT-02", "LEGACY-01"}, "active product receipt lost construction authority")
    else:
        require(receipt_status == "INACTIVE", f"{lifecycle_state} cannot carry an ACTIVE construction receipt")
        require(receipt.get("product_code_allowed") is False, f"{lifecycle_state} receipt cannot authorize product construction")
        require(receipt.get("legacy_admission_allowed") is False, f"{lifecycle_state} receipt cannot authorize legacy admission")
        require(not receipt.get("incident_repair_ids"), f"{lifecycle_state} INACTIVE receipt cannot carry incident repair authority")

    reconstruction = handoff["reconstruction"]
    require(reconstruction.get("handoff_first") is True, "runtime reconstruction must start from durable handoff")
    require(reconstruction.get("fresh_agent_requires_prior_chat") is False, "runtime reconstruction cannot require prior chat")
    refs = reconstruction["required_refs"]
    missing = [rel for rel in refs if not (repo / rel).exists()]
    require(not missing, f"handoff references missing authority: {', '.join(missing)}")
    supervision = build_supervision_summary(repo, current)

    runtime = {
        "schema_version": 5,
        "repository": handoff["repository"],
        "observed_head_sha": head,
        "head_binding": "RUNTIME_EXACT",
        "delivery": handoff["delivery"],
        "lifecycle": lifecycle,
        "controller": handoff["controller"],
        "construction_receipt_status": receipt_status,
        "incident_repair": repair_summary,
        "open_incidents": open_incidents,
        "canonical_scope": canonical_scope,
        "next_admissible_action": next_admissible_action,
        "required_refs": refs,
        "required_reconstruction_outcomes": reconstruction.get("required_outcomes", []),
        "fresh_agent_requires_prior_chat": False,
        "supervision": supervision,
        "coordination_contracts": {
            "action_receipt": {
                "id": action_contract["contract_id"],
                "required_request_fields": action_contract.get("required_request_fields", []),
                "required_result_fields": action_contract.get("required_result_fields", []),
                "trace_survives_hops": action_contract.get("traceability", {}).get("must_survive_hops", []),
                "memory_consultation": action_contract.get("memory_consultation", {}),
            },
            "acceptance_evidence": {
                "id": acceptance_spine["spine_id"],
                "states": acceptance_spine.get("states", []),
                "health_mapping": acceptance_spine.get("health_mapping", {}),
                "memory_retention": acceptance_spine.get("memory_retention", {}),
            },
        },
        "archaeology_fallback": reconstruction["archaeology_fallback"],
    }
    return runtime


def build_observation(runtime: dict) -> dict:
    exact_checkout = _step_outcome("N0TE2_EXACT_CHECKOUT_OUTCOME")
    governance = _step_outcome("N0TE2_GOVERNANCE_OUTCOME")
    context = _step_outcome("N0TE2_CONTEXT_LIFECYCLE_OUTCOME")
    supervision = _step_outcome("N0TE2_SUPERVISION_OUTCOME")
    handoff = _step_outcome("N0TE2_HANDOFF_OUTCOME")
    regression = _step_outcome("N0TE2_REGRESSION_OUTCOME")
    smoke = _step_outcome("N0TE2_SMOKE_OUTCOME")
    return {
        "schema_version": 5,
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
            "authority_checked": governance["ran"],
            "exact_head_checked": exact_checkout["ran"],
            "context_lifecycle_checked": context["ran"],
            "supervision_graph_checked": supervision["ran"],
            "action_receipt_contract_checked": handoff["ran"],
            "acceptance_evidence_spine_checked": handoff["ran"],
            "regression_tests_checked": regression["ran"],
            "consumer_smoke_checked": smoke["ran"],
            "step_outcomes": {
                "exact_head": exact_checkout,
                "construction_governance": governance,
                "context_lifecycle": context,
                "supervision": supervision,
                "runtime_handoff": handoff,
                "regression_tests": regression,
                "consumer_smoke": smoke,
            },
            "construction_receipt_status": runtime["construction_receipt_status"],
            "lifecycle_mode": runtime["lifecycle"]["mode"],
            "incident_repair": runtime.get("incident_repair"),
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
                f"mode={runtime['lifecycle']['mode']} active={runtime['lifecycle'].get('active_node') or '-'} "
                f"receipt={runtime['construction_receipt_status']} actors={len(runtime['supervision']['actors'])}"
            )
        elif not args.output and not args.observation_output:
            print(json.dumps(runtime, indent=2, sort_keys=True))
        return 0
    except (HandoffError, subprocess.CalledProcessError) as exc:
        print(f"N0TE2 HANDOFF: RED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
