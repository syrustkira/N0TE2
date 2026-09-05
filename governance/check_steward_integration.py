#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HEX40 = re.compile(r"^[0-9a-f]{40}$")
TERMINAL_LIFECYCLE_STATES = {"STABLE", "WAITING", "BLOCKED"}
ZERO_SHA = "0" * 40
CONSTRUCTION_SENSITIVE_EXACT = {
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
}
CANONICAL_SOURCE = "N0TE_PRODUCT_DB/SCOPE_LEDGER"
CANONICAL_SOURCE_REVISION = "524"
CANONICAL_EXTENSION_ROWS = [
    {
        "id": "REQ-SCOPE-154",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["UX-01", "CORE-02", "CORE-03", "CORE-04"],
        "summary": "Creative Partner professional-lens selection and bounded multi-perspective synthesis.",
    },
    {
        "id": "REQ-SCOPE-155",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["UX-01", "CORE-04"],
        "summary": "Creative Partner presence, initiative and silence policy including explicit no-action posture.",
    },
    {
        "id": "REQ-SCOPE-156",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["UX-01", "CORE-01", "CORE-02"],
        "summary": "Relevance brokerage and lens-sensitive bounded context projection.",
    },
    {
        "id": "REQ-SCOPE-157",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["UX-01", "CORE-01", "CORE-02", "CORE-04"],
        "summary": "Versioned backward-readable Creative Partner InteractionPolicy with ephemeral-first lifecycle and stale-state safety.",
    },
    {
        "id": "REQ-SCOPE-158",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["UX-01", "CORE-02"],
        "summary": "Creative tension, tradeoff and Challenger reasoning with simulated-versus-observed audience truth.",
    },
    {
        "id": "REQ-SCOPE-159",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["CONV-01", "CORE-01", "CORE-02", "CORE-04"],
        "summary": "Stable semantic keys and versioned definition-lineage evolution.",
    },
    {
        "id": "REQ-SCOPE-160",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["DAW-07", "CORE-03", "CORE-04", "CONV-01"],
        "summary": "Provider/host/protocol-neutral negotiated capability with versioned adapters, fidelity truth and route receipts.",
    },
    {
        "id": "REQ-SCOPE-161",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["ART-01", "OPS-04", "OPS-06", "CORE-02"],
        "summary": "Canonical Music Professional Master Map role ontology across the music value network.",
    },
    {
        "id": "REQ-SCOPE-162",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["OPS-05", "CORE-04", "CONV-01"],
        "summary": "Occupational music safety across role-specific hearing, physical, service and operational risks.",
    },
    {
        "id": "REQ-SCOPE-163",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["OPS-04", "OPS-06", "CORE-02"],
        "summary": "Professional portfolio, credits, reputation and referral evidence.",
    },
    {
        "id": "REQ-SCOPE-164",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["OPS-06", "CORE-02"],
        "summary": "Role-specific economics for music-professional jobs and career decisions.",
    },
    {
        "id": "REQ-SCOPE-165",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["OPS-04", "CORE-04"],
        "summary": "Cross-role handoff contracts that preserve responsibility, artifacts, authority and next-owner clarity.",
    },
    {
        "id": "REQ-SCOPE-166",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["CORE-04", "OPS-04", "OPS-05"],
        "summary": "Professional ethics and duty-of-care boundaries for role-aware work.",
    },
    {
        "id": "REQ-SCOPE-167",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["OPS-06", "CORE-02", "ART-01"],
        "summary": "Role development, skill progression and transitions across music careers.",
    },
    {
        "id": "REQ-SCOPE-168",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["APP-01", "PLATFORM-00", "CORE-04", "OPS-05"],
        "summary": "Physical and service resilience for professional music workflows.",
    },
    {
        "id": "REQ-SCOPE-169",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["OPS-04", "OPS-02", "CONV-01"],
        "summary": "Territory, international and cultural context for professional music work.",
    },
    {
        "id": "REQ-SCOPE-170",
        "state": "MAPPED",
        "selected": False,
        "construction_affinity": ["LATER-01", "OPS-02", "OPS-06"],
        "summary": "Legacy, catalog and career succession continuity; accepted but dependency-gated/later.",
    },
]


class StewardIntegrationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise StewardIntegrationError(message)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except Exception as exc:
        raise StewardIntegrationError(f"cannot load {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT
    ).strip()


def check_canonical_extensions(repo: Path) -> None:
    doc = load_json(repo / "governance/requirements.json")
    graph_doc = load_json(repo / "governance/completion_graph.json")
    graph_ids = {
        row.get("id")
        for row in graph_doc.get("nodes", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }

    sequence = doc.get("sequence", {})
    canonical = doc.get("canonical_scope", {})
    require(doc.get("sequence_role") == "BUILD_GRAPH_INDEX", "requirement sequence role changed")
    require(sequence.get("start") == 2 and sequence.get("end") == 153, "build-graph requirement index changed unexpectedly")
    require(canonical.get("source") == CANONICAL_SOURCE, "canonical requirement source changed")
    require(canonical.get("source_revision") == CANONICAL_SOURCE_REVISION, "canonical requirement source revision changed without a reviewed Steward manifest update")
    require(canonical.get("start") == 2 and canonical.get("end") == 170, "canonical retained scope changed without a reviewed Steward manifest update")
    require(canonical.get("retained_requirement_count") == 169, "canonical retained requirement count is inconsistent")

    extensions = doc.get("canonical_extensions")
    require(isinstance(extensions, list), "canonical_extensions must be a list")
    require(extensions == CANONICAL_EXTENSION_ROWS, "canonical extension semantics or construction affinities changed without a reviewed Steward manifest update")

    for row in extensions:
        requirement_id = row["id"]
        affinity = row["construction_affinity"]
        require(
            all(node_id in graph_ids for node_id in affinity),
            f"{requirement_id} references an unknown construction-affinity node",
        )


def construction_sensitive(path: str) -> bool:
    if path.startswith("n0te2/"):
        return True
    if path.startswith("tests/") and not path.startswith("tests/governance/"):
        return True
    return path in CONSTRUCTION_SENSITIVE_EXACT


def candidate_base(repo: Path) -> str | None:
    supplied_raw = str(os.environ.get("N0TE2_BASE_SHA") or "").strip().lower()
    if supplied_raw:
        require(HEX40.match(supplied_raw) is not None, "N0TE2_BASE_SHA must be an exact lowercase 40-character SHA")
        require(supplied_raw != ZERO_SHA, "all-zero candidate base is unverifiable; refuse to publish a Steward structural pass")
        return supplied_raw
    try:
        return git(repo, "rev-parse", "HEAD^")
    except subprocess.CalledProcessError:
        return None


def candidate_changed_paths(repo: Path) -> list[str]:
    base = candidate_base(repo)
    if base is None:
        try:
            changed = git(repo, "diff-tree", "--root", "--no-commit-id", "--no-renames", "--name-only", "-r", "HEAD")
        except subprocess.CalledProcessError as exc:
            raise StewardIntegrationError(f"cannot derive full root candidate diff: {exc.output}") from exc
    else:
        try:
            changed = git(repo, "diff", "--no-renames", "--name-only", f"{base}...HEAD")
        except subprocess.CalledProcessError as exc:
            raise StewardIntegrationError(
                f"cannot derive candidate diff from base {base}: {exc.output}"
            ) from exc
    return [path for path in changed.splitlines() if path]


def check_terminal_construction_gate(repo: Path, verify_git: bool) -> None:
    current = load_json(repo / "governance/current_state.json")
    lifecycle = current.get("lifecycle_state")
    if lifecycle not in TERMINAL_LIFECYCLE_STATES or not verify_git:
        return

    require(git(repo, "rev-parse", "--is-inside-work-tree") == "true", "not a git worktree")
    expected_head = str(os.environ.get("N0TE2_HEAD_SHA") or "").strip().lower()
    if expected_head:
        require(HEX40.match(expected_head) is not None, "N0TE2_HEAD_SHA must be an exact lowercase 40-character SHA")
        actual_head = git(repo, "rev-parse", "HEAD")
        require(actual_head == expected_head, f"exact-head mismatch: expected {expected_head}, got {actual_head}")

    changed = candidate_changed_paths(repo)
    construction = [path for path in changed if construction_sensitive(path)]
    require(
        not construction,
        f"{lifecycle} candidate changed construction-sensitive paths without ACTIVE bounded receipt: {', '.join(construction)}",
    )


def check_merge_policy(repo: Path) -> None:
    policy = load_json(repo / "governance/merge_policy.json")
    requirements = policy.get("requirements", {})
    for key in (
        "exact_head_only",
        "handoff_consistent",
        "governance_green",
        "full_regression_green",
        "consumer_smoke_green",
        "blocking_incidents_resolved_before_merge",
        "draft_pr_cannot_merge",
        "substantive_review_terminal_on_exact_head",
        "review_findings_resolved_or_dispositioned",
        "late_live_main_race_check",
        "expected_head_guarded_merge",
        "post_merge_verification",
        "single_main_steward_writer",
        "late_review_creates_post_merge_incident",
    ):
        require(requirements.get(key) is True, f"merge policy missing required Steward gate: {key}")

    steward_gate = policy.get("steward_gate", {})
    require(steward_gate.get("required") is True, "Steward integration gate must be required")
    require(steward_gate.get("structural_status_context") == "n0te2-steward-structure", "unexpected trusted Steward structural status context")
    require(steward_gate.get("bootstrap_status_context") == "n0te2-steward-bootstrap-structure", "unexpected Steward bootstrap status context")
    require(steward_gate.get("status_is_merge_authorization") is False, "a static Steward status must never be represented as live merge authorization")
    require(steward_gate.get("live_authorization_owner") == "MAIN_STEWARD", "live merge authorization must remain owned by the Main Steward")
    require(steward_gate.get("trusted_checker_source") == "PR_BASE", "future PR enforcement must execute a checker from the trusted PR base")
    require(steward_gate.get("trusted_workflow_event") == "pull_request_target", "future trusted Steward enforcement must run from the base-owned workflow")
    require(steward_gate.get("bootstrap_requires_manual_steward_review") is True, "Steward gate bootstrap must require manual live Steward review")
    require(steward_gate.get("pending_review_blocks_merge") is True, "pending substantive review must block Steward merge authorization")
    require(steward_gate.get("review_must_bind_exact_head") is True, "substantive review must bind exact candidate head")
    require(steward_gate.get("post_merge_review_opens_fix_order") is True, "late review must create a durable post-merge repair obligation")


def check_steward_actor(repo: Path) -> None:
    registry = load_json(repo / "governance/automation_registry.json")
    actors = registry.get("actors", [])
    actor = next(
        (row for row in actors if isinstance(row, dict) and row.get("id") == "AUTO-STEWARD-INTEGRATION-GATE-001"),
        None,
    )
    require(actor is not None, "Steward integration workflow is missing from the supervision graph")
    require(actor.get("path") == ".github/workflows/steward-integration.yml", "Steward integration actor path changed")
    require(actor.get("authority") == "VERIFY_STRUCTURE_ONLY", "Steward workflow must not claim live merge authority")
    require(actor.get("parent") == "N0TE-SUPERVISOR", "Steward workflow escaped supervision parent")
    observability = actor.get("observability", {})
    require(observability.get("exact_head_required") is True, "Steward workflow lacks exact-head observability")
    require(observability.get("reactivation_is_event") is True, "Steward workflow may reactivate silently")
    require(observability.get("trusted_status_context") == "n0te2-steward-structure", "Steward workflow trusted status context changed")
    require(observability.get("bootstrap_status_context") == "n0te2-steward-bootstrap-structure", "Steward workflow bootstrap status context changed")


def run(repo: Path, verify_git: bool = True) -> None:
    check_canonical_extensions(repo)
    check_merge_policy(repo)
    check_steward_actor(repo)
    check_terminal_construction_gate(repo, verify_git)
    print("N0TE2 STEWARD INTEGRATION STRUCTURE: GREEN")
    print("merge_authorization=LIVE_MAIN_STEWARD_ONLY")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--no-git", action="store_true")
    args = parser.parse_args()
    try:
        run(Path(args.repo).resolve(), verify_git=not args.no_git)
    except StewardIntegrationError as exc:
        print(f"N0TE2 STEWARD INTEGRATION STRUCTURE: RED: {exc}", file=sys.stderr)
        sys.exit(1)
