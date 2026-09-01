#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from supervision import SupervisionError, inspect_supervision


class ContextGovernanceError(RuntimeError):
    pass


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ContextGovernanceError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContextGovernanceError(f"{path} must contain a JSON object")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContextGovernanceError(message)


def check_invariants(repo: Path) -> None:
    registry = load_json(repo / "governance/invariants.json")
    rows = registry.get("constitutional", [])
    by_id = {row.get("id"): row for row in rows}
    required = {
        "INV-SUP-003": "No unjustified persistent loop.",
        "INV-SUP-004": "All automation reports into the supervision graph.",
        "INV-LIFE-003": "Construction must terminate at stability.",
        "INV-CTX-001": "Flattening is a projection, never destruction of the record.",
        "INV-CTX-002": "Conversation history is provenance, not authority; durable promotion requires explicit acceptance or canonical evidence.",
    }
    for invariant_id, statement in required.items():
        require(invariant_id in by_id, f"required invariant missing: {invariant_id}")
        require(by_id[invariant_id].get("statement") == statement, f"required invariant text drifted: {invariant_id}")
        require(by_id[invariant_id].get("change_class") == "CONSTITUTIONAL", f"{invariant_id} is not constitutional")
    require(
        registry.get("required_verbatim_doctrine") == [
            "No unjustified persistent loop.",
            "All automation reports into the supervision graph.",
            "Construction must terminate at stability.",
        ],
        "required supervision doctrine was flattened or reordered",
    )


def check_context_policy(repo: Path) -> None:
    policy = load_json(repo / "governance/context_lifecycle.json")
    require(policy.get("policy_id") == "CTX-LIFECYCLE-001", "unexpected context lifecycle policy")
    require(
        policy.get("constitutional_rule") == "Flattening is a projection, never destruction of the record.",
        "flattening constitutional rule drifted",
    )
    require(
        policy.get("states") == ["RAW", "ACTIVE", "SUMMARIZED", "REFERENCE", "ARCHIVED"],
        "context lifecycle states changed without migration",
    )
    projection = policy.get("projection_contract", {})
    require(projection.get("canonical_sources_remain_authoritative") is True, "projection became canonical authority")
    require(projection.get("projection_is_disposable") is True, "projection must remain disposable")
    require(projection.get("projection_is_regenerable") is True, "projection must remain regenerable")
    require(projection.get("projection_grants_action_authority") is False, "projection gained action authority")
    required_metadata = {
        "scope",
        "purpose",
        "policy_version",
        "authority_ceiling",
        "source_manifest",
        "source_digest",
        "lossiness",
        "selected_sections",
        "budget",
        "contradictions",
    }
    require(required_metadata.issubset(set(projection.get("must_record", []))), "projection provenance metadata narrowed")
    protected = {
        "UNRESOLVED_CONTRADICTION",
        "AUTHORITY_DECISION",
        "ACTIVE_RECEIPT",
        "OPEN_INCIDENT",
        "ACCEPTANCE_EVIDENCE",
        "CONSTITUTIONAL_DEFINITION",
    }
    require(protected.issubset(set(projection.get("must_not_flatten_away", []))), "critical context may be flattened away")

    retrieval = policy.get("retrieval_policy", {})
    require(retrieval.get("default") == "SELECTIVE", "retrieval must remain selective")
    require(retrieval.get("dump_all_history_by_default") is False, "megacontext retrieval was reintroduced")
    require(
        retrieval.get("ranking") == [
            "AUTHORITY",
            "CURRENT_SCOPE",
            "UNRESOLVED_STATUS",
            "EVIDENCE_CONFIDENCE",
            "RECENCY",
        ],
        "context retrieval precedence drifted",
    )

    distillation = policy.get("conversation_distillation", {})
    require(distillation.get("conversation_is_provenance_not_authority") is True, "conversation history became hidden authority")
    require(distillation.get("automatic_durable_promotion") is False, "conversation may not auto-promote into durable authority")
    required_promotion = {
        "EXPLICIT_ACCEPTANCE_OR_CANONICAL_EVIDENCE",
        "TARGET_LEDGER",
        "SOURCE_PROVENANCE",
        "AUTHORITY_CLASS",
        "SUPERSESSION_POSTURE",
    }
    require(required_promotion.issubset(set(distillation.get("durable_promotion_requires", []))), "durable conversation promotion contract narrowed")

    contradiction = policy.get("contradiction_policy", {})
    require(contradiction.get("average_conflicting_claims") is False, "conflicting context may not be averaged")
    require(contradiction.get("unresolved_critical_contradiction_blocks_autonomous_mutation") is True, "critical contradictions must block autonomous mutation")
    require(contradiction.get("contradiction_must_remain_visible") is True, "contradiction visibility was disabled")

    gc = policy.get("semantic_gc", {})
    require(gc.get("default_action") == "REMOVE_FROM_ACTIVE_RETRIEVAL", "semantic GC must prune retrieval, not history")
    require(gc.get("delete_canonical_history_by_default") is False, "semantic GC may not delete canonical history by default")
    require(gc.get("audit_history_retained") is True, "semantic GC lost audit history")

    migration = policy.get("constitutional_change_protocol", {})
    protected_concepts = {"DONE", "AUTHORITY", "MUTATION_PERMISSION", "ACCEPTANCE", "STABLE", "USER_CONTROL"}
    require(protected_concepts.issubset(set(migration.get("protected_concepts", []))), "constitutional definition protection narrowed")
    require(migration.get("silent_change_allowed") is False, "constitutional definitions may not silently drift")
    require(
        {"EXPLICIT_APPROVAL", "VERSIONED_DEFINITION", "IMPACT_RECORD", "MIGRATION_OR_COMPATIBILITY_PLAN", "PROVENANCE"}.issubset(
            set(migration.get("requires", []))
        ),
        "constitutional migration protocol is incomplete",
    )

    reconstruction = policy.get("reconstruction_contract", {})
    require(reconstruction.get("fresh_agent_must_not_require_prior_chat") is True, "fresh-agent reconstruction still depends on chat")
    require(reconstruction.get("startup_surface") == "governance/handoff.json", "fresh-agent startup must begin at handoff")
    require(
        reconstruction.get("historical_archaeology_only_for") == [
            "MISSING_DURABLE_AUTHORITY",
            "CONTRADICTORY_DURABLE_AUTHORITY",
        ],
        "historical archaeology became a normal reconstruction path",
    )


def check_authority_and_handoff(repo: Path) -> None:
    authority = load_json(repo / "governance/authority.json")
    files = set(authority.get("current_authority_files", []))
    require("governance/context_lifecycle.json" in files, "context lifecycle is not in current authority")
    require("governance/supervision.py" in files, "supervision inspector is not in current authority")
    laws = authority.get("laws", {})
    require(laws.get("context_projection_is_not_canonical_authority") is True, "projection authority law missing")
    require(laws.get("conversation_is_provenance_not_authority") is True, "conversation provenance law missing")
    require(laws.get("semantic_gc_preserves_canonical_history") is True, "semantic GC history law missing")
    require(laws.get("critical_contradiction_blocks_autonomous_mutation") is True, "contradiction safety law missing")

    handoff = load_json(repo / "governance/handoff.json")
    refs = set(handoff.get("reconstruction", {}).get("required_refs", []))
    require("governance/context_lifecycle.json" in refs, "handoff does not reconstruct context policy")
    require("governance/supervision.py" in refs, "handoff does not reconstruct supervision surface")


def run(repo: Path) -> None:
    check_invariants(repo)
    check_context_policy(repo)
    check_authority_and_handoff(repo)
    try:
        inspect_supervision(repo)
    except SupervisionError as exc:
        raise ContextGovernanceError(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    try:
        run(repo)
    except ContextGovernanceError as exc:
        print(f"N0TE2 CONTEXT GOVERNANCE: RED: {exc}", file=sys.stderr)
        return 1
    print("N0TE2 CONTEXT GOVERNANCE: GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
