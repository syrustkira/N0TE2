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
    require(canonical.get("source") == "N0TE_PRODUCT_DB/SCOPE_LEDGER", "canonical requirement source changed")
    require(canonical.get("start") == sequence.get("start"), "canonical scope start must match build-graph index start")

    canonical_end = canonical.get("end")
    require(type(canonical_end) is int and canonical_end >= 170, "canonical retained scope cannot shrink below REQ-SCOPE-170")
    retained = canonical_end - int(canonical["start"]) + 1
    require(canonical.get("retained_requirement_count") == retained, "canonical retained requirement count is inconsistent")

    extensions = doc.get("canonical_extensions")
    require(isinstance(extensions, list), "canonical_extensions must be a list")
    expected_ids = [
        f"REQ-SCOPE-{number:03d}"
        for number in range(int(sequence["end"]) + 1, canonical_end + 1)
    ]
    actual_ids = [row.get("id") if isinstance(row, dict) else None for row in extensions]
    require(actual_ids == expected_ids, "canonical extension IDs must remain contiguous, ordered, unique, and complete")

    for row in extensions:
        requirement_id = row["id"]
        require(row.get("state") == "MAPPED", f"{requirement_id} must remain MAPPED")
        require(row.get("selected") is False, f"{requirement_id} cannot self-select construction")
        summary = row.get("summary")
        require(isinstance(summary, str) and summary.strip(), f"{requirement_id} requires a retained summary")
        affinity = row.get("construction_affinity")
        require(isinstance(affinity, list) and affinity, f"{requirement_id} requires construction affinity")
        require(
            all(isinstance(node_id, str) and node_id in graph_ids for node_id in affinity),
            f"{requirement_id} references an unknown construction-affinity node",
        )
        require(len(affinity) == len(set(affinity)), f"{requirement_id} contains duplicate construction-affinity nodes")


def construction_sensitive(path: str) -> bool:
    if path.startswith("n0te2/"):
        return True
    if path.startswith("tests/") and not path.startswith("tests/governance/"):
        return True
    return path in CONSTRUCTION_SENSITIVE_EXACT


def candidate_base(repo: Path) -> str | None:
    supplied = str(os.environ.get("N0TE2_BASE_SHA") or "").strip().lower()
    if supplied and supplied != ZERO_SHA:
        require(HEX40.match(supplied) is not None, "N0TE2_BASE_SHA must be an exact lowercase 40-character SHA")
        return supplied
    try:
        return git(repo, "rev-parse", "HEAD^")
    except subprocess.CalledProcessError:
        return None


def candidate_changed_paths(repo: Path) -> list[str]:
    base = candidate_base(repo)
    if base is None:
        return []
    try:
        changed = git(repo, "diff", "--name-only", f"{base}...HEAD")
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
    require(steward_gate.get("status_context") == "n0te2-steward-integration", "unexpected Steward status context")
    require(steward_gate.get("pending_review_blocks_merge") is True, "pending substantive review must block Steward merge authorization")
    require(steward_gate.get("review_must_bind_exact_head") is True, "substantive review must bind exact candidate head")
    require(steward_gate.get("post_merge_review_opens_fix_order") is True, "late review must create a durable post-merge repair obligation")


def run(repo: Path, verify_git: bool = True) -> None:
    check_canonical_extensions(repo)
    check_merge_policy(repo)
    check_terminal_construction_gate(repo, verify_git)
    print("N0TE2 STEWARD INTEGRATION: GREEN")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--no-git", action="store_true")
    args = parser.parse_args()
    try:
        run(Path(args.repo).resolve(), verify_git=not args.no_git)
    except StewardIntegrationError as exc:
        print(f"N0TE2 STEWARD INTEGRATION: RED: {exc}", file=sys.stderr)
        sys.exit(1)
