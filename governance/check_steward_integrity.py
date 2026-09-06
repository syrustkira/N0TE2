#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HEX40 = re.compile(r"^[0-9a-f]{40}$")
REQ_ID = re.compile(r"^REQ-SCOPE-(\d{3})$")
REQ_TOKEN = re.compile(r"^(?:REQ-SCOPE-)?(\d{3})(?:-(?:REQ-SCOPE-)?(\d{3}))?$")

ACCEPTANCE_STATES = [
    "MAPPED",
    "IMPLEMENTED",
    "INTEGRATED",
    "REACHABLE",
    "VERIFIED",
    "RECOVERABLE",
    "AUTHORITY_SAFE",
    "CONSUMER_ACCEPTED",
    "VALUE_EVIDENCED",
]
ACCEPTANCE_LINKS = [
    "requirement_id",
    "canonical_scope_ref",
    "implementation_refs",
    "integration_refs",
    "user_reachability_refs",
    "verification_refs",
    "failure_recovery_refs",
    "authority_security_refs",
    "consumer_acceptance_refs",
    "value_evidence_refs",
]
DISCOVERY_DISPOSITIONS = {
    "EXECUTED",
    "DURABLY_CAPTURED",
    "DUPLICATE",
    "BLOCKED",
    "REJECTED",
    "NON_ACTIONABLE",
}
EQUIV_FIELDS = {
    "original_requirement",
    "replacement_implementation",
    "semantic_coverage_mapping",
    "retained_behavior",
    "changed_behavior",
    "uncovered_residue",
    "acceptance_evidence",
    "semantic_authority",
    "lineage",
    "successor_status",
}


class StewardIntegrityError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise StewardIntegrityError(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise StewardIntegrityError(f"cannot load {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path} must contain an object")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            value = json.loads(raw)
            require(isinstance(value, dict), f"{path}:{lineno} is not an object")
            rows.append(value)
    except Exception as exc:
        raise StewardIntegrityError(f"cannot load {path}: {exc}") from exc
    return rows


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=repo,
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def canonical_requirement_id(number: int) -> str:
    return f"REQ-SCOPE-{number:03d}"


def expand_requirement_spec(value: Any) -> set[str]:
    if value in (None, ""):
        return set()
    if isinstance(value, list):
        expanded: set[str] = set()
        for item in value:
            expanded |= expand_requirement_spec(item)
        return expanded
    require(isinstance(value, str), "requirement expression must be text or list")

    expanded: set[str] = set()
    for raw in value.split(","):
        token = raw.strip()
        if not token:
            continue
        match = REQ_TOKEN.fullmatch(token)
        require(match is not None, f"invalid requirement token: {token}")
        start = int(match.group(1))
        end = int(match.group(2) or match.group(1))
        require(start <= end, f"descending requirement range: {token}")
        expanded |= {canonical_requirement_id(number) for number in range(start, end + 1)}
    return expanded


def normalize_requirement_ids(values: Any, field: str = "requirements") -> list[str]:
    if values is None:
        return []
    require(isinstance(values, list), f"{field} must be a list")
    require(
        all(isinstance(value, str) and REQ_ID.fullmatch(value.strip()) for value in values),
        f"{field} contains malformed ids",
    )
    normalized = [value.strip() for value in values]
    require(len(normalized) == len(set(normalized)), f"{field} contains duplicates")
    return normalized


def graph_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = graph.get("nodes")
    require(isinstance(rows, list), "completion_graph.nodes must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        require(isinstance(row, dict), "completion graph node must be an object")
        node_id = row.get("id")
        require(isinstance(node_id, str) and node_id, "completion graph node lacks id")
        require(node_id not in indexed, f"duplicate completion graph node id: {node_id}")
        indexed[node_id] = row
    return indexed


def validate_contract(contract: dict[str, Any]) -> None:
    require(contract.get("schema_version") == 1, "integrity contract schema must be 1")
    require(
        contract.get("contract_id") == "STEWARD-CROSS-LEDGER-INTEGRITY-001",
        "unexpected integrity contract id",
    )
    require(
        contract.get("role") == "VALIDATION_ONLY_NO_SEMANTIC_AUTHORITY",
        "integrity checker became semantic authority",
    )
    boundary = contract.get("authority_boundary", {})
    require(boundary.get("creates_second_steward") is False, "integrity checker cannot create a second Steward")
    require(boundary.get("can_mutate_main") is False, "integrity checker cannot mutate main")
    require(boundary.get("can_accept_artist_value") is False, "integrity checker cannot fabricate artist acceptance")
    legacy = contract.get("legacy_compatibility", {})
    require(legacy.get("new_supersession_requires_evidence") is True, "new supersession must require evidence")
    require(
        legacy.get("historical_supersession_without_local_proof_is_not_equivalence") is True,
        "historical supersession cannot become implicit equivalence",
    )


def validate_requirement_graph(
    requirements: dict[str, Any],
    graph: dict[str, Any],
) -> tuple[set[str], set[str], dict[str, dict[str, Any]]]:
    canonical = requirements.get("canonical_scope", {})
    start = canonical.get("start")
    end = canonical.get("end")
    count = canonical.get("retained_requirement_count")
    require(type(start) is int and type(end) is int and start <= end, "canonical range invalid")
    require(count == end - start + 1, "canonical retained count does not match range")
    canonical_ids = {canonical_requirement_id(number) for number in range(start, end + 1)}

    nodes = graph_index(graph)
    graph_requirements: set[str] = set()
    for node_id, row in nodes.items():
        graph_requirements |= expand_requirement_spec(row.get("requirements"))
        for field in ("depends_on", "any_of"):
            refs = row.get(field, []) or []
            require(isinstance(refs, list), f"{node_id}.{field} must be a list")
            for ref in refs:
                require(
                    isinstance(ref, str) and ref in nodes,
                    f"{node_id}.{field} references unknown node: {ref}",
                )
    unknown = sorted(graph_requirements - canonical_ids)
    require(
        not unknown,
        "completion graph references requirements outside canonical scope: " + ", ".join(unknown),
    )

    held = set(normalize_requirement_ids(requirements.get("held_or_boundary"), "held_or_boundary"))
    require(held <= canonical_ids, "held scope escaped canonical range")
    require("LATER-01" in nodes, "completion graph lost LATER-01")
    require(
        expand_requirement_spec(nodes["LATER-01"].get("requirements")) == held,
        "held_or_boundary and LATER-01 diverged; retained work may have been silently dropped or invented",
    )

    superseded = set(normalize_requirement_ids(requirements.get("superseded"), "superseded"))
    require(superseded <= canonical_ids, "superseded scope escaped canonical range")
    require(not held & superseded, "requirement cannot be held and superseded")
    require(not graph_requirements & superseded, "superseded requirement still appears in construction graph")

    extensions = requirements.get("canonical_extensions")
    require(isinstance(extensions, list), "canonical_extensions must be a list")
    seen: set[str] = set()
    for row in extensions:
        require(isinstance(row, dict), "canonical extension must be an object")
        requirement_id = row.get("id")
        require(
            isinstance(requirement_id, str) and REQ_ID.fullmatch(requirement_id),
            "malformed canonical extension id",
        )
        require(
            requirement_id in canonical_ids and requirement_id not in seen,
            f"invalid/duplicate canonical extension: {requirement_id}",
        )
        seen.add(requirement_id)
        require(
            row.get("state") == "MAPPED" and row.get("selected") is False,
            f"canonical extension {requirement_id} self-selected or changed state",
        )
        affinities = row.get("construction_affinity")
        require(
            isinstance(affinities, list) and affinities,
            f"canonical extension {requirement_id} lacks affinity",
        )
        require(
            all(affinity in nodes for affinity in affinities),
            f"canonical extension {requirement_id} references unknown affinity",
        )

    sequence = requirements.get("sequence", {})
    require(
        type(sequence.get("start")) is int and type(sequence.get("end")) is int,
        "build sequence invalid",
    )
    require(
        start <= sequence["start"] <= sequence["end"] <= end,
        "build sequence escaped canonical range",
    )
    return canonical_ids, superseded, nodes


def validate_decisions(rows: list[dict[str, Any]]) -> None:
    by_id: dict[str, dict[str, Any]] = {}
    successors: dict[str, list[str]] = {}
    for row in rows:
        decision_id = row.get("id")
        require(isinstance(decision_id, str) and decision_id, "decision row lacks id")
        require(decision_id not in by_id, f"duplicate decision id: {decision_id}")
        by_id[decision_id] = row
    for decision_id, row in by_id.items():
        refs = row.get("supersedes", []) or []
        require(isinstance(refs, list), f"{decision_id}.supersedes must be a list")
        for ref in refs:
            require(ref in by_id and ref != decision_id, f"{decision_id} supersedes invalid decision: {ref}")
            successors.setdefault(ref, []).append(decision_id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(decision_id: str) -> None:
        if decision_id in visited:
            return
        require(decision_id not in visiting, f"decision supersession cycle detected at {decision_id}")
        visiting.add(decision_id)
        for ref in by_id[decision_id].get("supersedes", []) or []:
            visit(ref)
        visiting.remove(decision_id)
        visited.add(decision_id)

    for decision_id in by_id:
        visit(decision_id)
    for decision_id, row in by_id.items():
        if str(row.get("status") or "").upper() == "SUPERSEDED":
            require(successors.get(decision_id), f"superseded decision lacks durable successor: {decision_id}")


def current_incidents(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        incident_id = row.get("id")
        require(isinstance(incident_id, str) and incident_id, "incident row lacks id")
        latest[incident_id] = row
    return latest


def validate_equivalence_receipt(value: Any, canonical_ids: set[str]) -> None:
    require(isinstance(value, dict), "SUPERSEDED_BY_EQUIVALENT requires equivalence_receipt evidence")
    missing = sorted(EQUIV_FIELDS - set(value))
    require(not missing, "equivalence_receipt missing required fields: " + ", ".join(missing))
    require(value.get("original_requirement") in canonical_ids, "equivalence original requirement must be canonical")
    for key in (
        "replacement_implementation",
        "semantic_coverage_mapping",
        "semantic_authority",
        "lineage",
        "successor_status",
    ):
        require(bool(value.get(key)), f"equivalence_receipt {key} must be non-empty")


def validate_receipt_and_current_state(
    current: dict[str, Any],
    receipt: dict[str, Any],
    canonical_ids: set[str],
    superseded: set[str],
    nodes: dict[str, dict[str, Any]],
    incidents: dict[str, dict[str, Any]],
) -> None:
    lifecycle = current.get("lifecycle_state")
    require(lifecycle in {"ACTIVE", "STABLE", "WAITING", "BLOCKED"}, f"unknown lifecycle_state: {lifecycle}")
    if lifecycle == "ACTIVE":
        require(receipt.get("status") == "ACTIVE", "ACTIVE current_state requires ACTIVE receipt")
        require(receipt.get("node_id") == current.get("active_node"), "ACTIVE receipt node diverges from current_state")
        require(receipt.get("increment_id") == current.get("active_increment"), "ACTIVE receipt increment diverges from current_state")
        baseline = receipt.get("baseline_sha")
        require(isinstance(baseline, str) and HEX40.fullmatch(baseline), "ACTIVE receipt must bind exact baseline SHA")
        if current.get("active_node") != "INCIDENT-REPAIR":
            require(current.get("active_node") in nodes, "ACTIVE work references unknown graph node")
    else:
        require(receipt.get("status") == "INACTIVE", f"{lifecycle} requires INACTIVE receipt")
        require(current.get("active_node") is None and current.get("active_increment") is None, f"{lifecycle} retained active identity")

    lineage = receipt.get("lineage", {})
    require(isinstance(lineage, dict), "receipt lineage must be an object")
    lineage_requirements = set(normalize_requirement_ids(lineage.get("requirements", []), "receipt lineage requirements"))
    require(lineage_requirements <= canonical_ids, "receipt lineage references non-canonical requirement")
    if lineage_requirements & superseded:
        validate_equivalence_receipt(receipt.get("equivalence_receipt"), canonical_ids)

    incident_ids = receipt.get("incident_repair_ids", []) or []
    require(isinstance(incident_ids, list), "incident_repair_ids must be a list")
    for incident_id in incident_ids:
        require(incident_id in incidents, f"repair receipt references unknown incident: {incident_id}")
        incident = incidents[incident_id]
        require(str(incident.get("status") or "").upper().startswith("OPEN"), f"repair receipt references non-open incident: {incident_id}")
        contract = incident.get("repair_contract")
        require(isinstance(contract, dict) and contract.get("future_receipt_field") == "incident_repair_ids", f"incident lacks receipt-bound repair contract: {incident_id}")

    if receipt.get("public_handoff_required") is True:
        public_ref = receipt.get("public_handoff_ref")
        require(isinstance(public_ref, str) and public_ref.strip(), "public-consequence receipt declared public_handoff_required but has no durable public_handoff_ref")
    require(str(receipt.get("public_acceptance_state") or "").upper() != "PUBLIC_VERIFIED", "repository integration receipt cannot create PUBLIC_VERIFIED")
    disposition = str(receipt.get("current_disposition") or receipt.get("disposition") or "").upper()
    if disposition == "SUPERSEDED_BY_EQUIVALENT":
        validate_equivalence_receipt(receipt.get("equivalence_receipt"), canonical_ids)


def validate_static_spines(repo: Path) -> None:
    spine = load_json(repo / "governance/acceptance_evidence_spine.json")
    require(spine.get("states") == ACCEPTANCE_STATES, "acceptance evidence states changed or collapsed")
    require(spine.get("required_links") == ACCEPTANCE_LINKS, "acceptance evidence links changed or collapsed")
    rules = "\n".join(str(value) for value in spine.get("rules", []))
    require("Foreground selection never removes retained requirements" in rules and "user reachability" in rules, "acceptance spine lost retention/reachability boundary")

    work = load_json(repo / "governance/work_continuity.json")
    require(set(work.get("legal_unfinished_states", [])) == {"ACTIVE", "WAITING", "BLOCKED"}, "work continuity lost unfinished states")
    checkpoint = set(work.get("required_checkpoint_fields", []))
    require({"canonical_work_id", "remaining_work", "wake_condition"} <= checkpoint, "work continuity lost checkpoint fields")

    discovery = load_json(repo / "governance/discovery_closure.json")
    require(set((discovery.get("allowed_dispositions") or {}).keys()) == DISCOVERY_DISPOSITIONS, "discovery closure dispositions changed")
    require(discovery.get("unfinished_work_policy") == "governance/work_continuity.json", "discovery closure lost work-continuity binding")

    continuity = load_json(repo / "governance/continuity_acceptance.json")
    seen: set[str] = set()
    for row in continuity.get("acceptance_items", []):
        require(isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"], "continuity acceptance item lacks id")
        require(row["id"] not in seen, f"duplicate continuity acceptance id: {row['id']}")
        seen.add(row["id"])
        require(isinstance(row.get("state"), str) and row["state"] and isinstance(row.get("requirement"), str) and row["requirement"], f"continuity acceptance {row['id']} lost state/requirement")

    action = load_json(repo / "governance/action_receipt_contract.json")
    require(action.get("contract_id") == "ACTION-RECEIPT-001", "action receipt contract changed")
    require({"operation_id", "trace_id", "authority_basis", "state_basis", "idempotency_key"} <= set(action.get("required_request_fields", [])), "action receipt request lost trace/authority/idempotency")
    require({"operation_id", "trace_id", "result_state", "evidence_refs", "reconciliation_required"} <= set(action.get("required_result_fields", [])), "action receipt result lost evidence/reconciliation")
    authority = action.get("authority_rules", {})
    require(authority.get("stale_receipt_authorizes_new_work") is False and authority.get("target_must_be_revalidated_before_external_consequence") is True, "action receipt authority boundary weakened")


def validate_new_supersessions(
    repo: Path,
    base_sha: str | None,
    requirements: dict[str, Any],
    receipt: dict[str, Any],
    canonical_ids: set[str],
) -> None:
    if not base_sha:
        return
    require(HEX40.fullmatch(base_sha) is not None, "N0TE2_BASE_SHA must be exact lowercase SHA")
    try:
        base = json.loads(git(repo, "show", f"{base_sha}:governance/requirements.json"))
    except Exception as exc:
        raise StewardIntegrityError(f"cannot inspect exact base requirements at {base_sha}: {exc}") from exc
    require(isinstance(base, dict), "exact base requirements must contain an object")
    old = set(normalize_requirement_ids(base.get("superseded"), "base superseded"))
    new = set(normalize_requirement_ids(requirements.get("superseded"), "candidate superseded")) - old
    if not new:
        return
    rows = receipt.get("equivalence_receipts")
    require(isinstance(rows, list), "new supersession requires active_receipt.equivalence_receipts")
    by_requirement: dict[str, dict[str, Any]] = {}
    for row in rows:
        validate_equivalence_receipt(row, canonical_ids)
        by_requirement[row["original_requirement"]] = row
    missing = sorted(new - set(by_requirement))
    require(not missing, "newly superseded requirements lack equivalence evidence: " + ", ".join(missing))


def run(repo: Path, *, verify_git: bool = True) -> None:
    validate_contract(load_json(repo / "governance/steward_integrity_contract.json"))
    requirements = load_json(repo / "governance/requirements.json")
    graph = load_json(repo / "governance/completion_graph.json")
    canonical_ids, superseded, nodes = validate_requirement_graph(requirements, graph)
    decisions = load_jsonl(repo / "governance/decisions.jsonl")
    validate_decisions(decisions)
    incidents = current_incidents(load_jsonl(repo / "governance/incidents.jsonl"))
    receipt = load_json(repo / "governance/active_receipt.json")
    current = load_json(repo / "governance/current_state.json")
    validate_receipt_and_current_state(current, receipt, canonical_ids, superseded, nodes, incidents)
    validate_static_spines(repo)
    base_sha = str(os.environ.get("N0TE2_BASE_SHA") or "").strip().lower() if verify_git else None
    validate_new_supersessions(repo, base_sha or None, requirements, receipt, canonical_ids)
    print(
        "N0TE2 STEWARD INTEGRITY: GREEN "
        f"canonical={len(canonical_ids)} "
        f"graph_nodes={len(nodes)} "
        f"held={len(requirements.get('held_or_boundary', []))} "
        f"decisions={len(decisions)} "
        f"incidents={len(incidents)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--no-git", action="store_true")
    args = parser.parse_args()
    try:
        run(Path(args.repo).resolve(), verify_git=not args.no_git)
        return 0
    except (StewardIntegrityError, subprocess.CalledProcessError) as exc:
        print(f"N0TE2 STEWARD INTEGRITY: RED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
