#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HEX40 = re.compile(r"^[0-9a-f]{40}$")
DECLARATION_PREFIX = "governance/builder_handoffs/"
DECLARATION_SUFFIX = ".builder.json"
STEWARD_META_EXACT = {
    ".github/workflows/steward-integration.yml",
    ".github/workflows/builder-handoff-qualification.yml",
    "governance/check_steward_integration.py",
    "governance/check_incident_repair_authority.py",
    "governance/check_steward_integrity.py",
    "governance/steward_integrity_contract.json",
    "governance/builder_handoff.py",
    "governance/builder_handoff_contract.json",
    "governance/check_builder_handoff_intake.py",
}


class BuilderIntakeError(RuntimeError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise BuilderIntakeError(message)


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=repo,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise BuilderIntakeError(
            f"git {' '.join(args)} failed: {exc.output.strip()}"
        ) from exc


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BuilderIntakeError(f"cannot load JSON {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


def load_trusted_builder_module():
    path = Path(__file__).with_name("builder_handoff.py")
    require(path.is_file(), f"trusted Builder handoff module missing: {path}")
    spec = importlib.util.spec_from_file_location("trusted_builder_handoff", path)
    require(spec is not None and spec.loader is not None, "cannot load trusted Builder handoff module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def changed_files(repo: Path, base_sha: str, head_sha: str) -> list[str]:
    raw = git(
        repo,
        "diff",
        "--no-renames",
        "--diff-filter=ACMR",
        "--name-only",
        f"{base_sha}...{head_sha}",
    )
    return [line.strip() for line in raw.splitlines() if line.strip()]


def role_for_candidate(repo: Path, changed: list[str]) -> str:
    if any(path in STEWARD_META_EXACT for path in changed):
        return "STEWARD_META_GOVERNANCE"

    receipt_path = repo / "governance" / "active_receipt.json"
    if receipt_path.is_file():
        receipt = load_json(receipt_path)
        if (
            receipt.get("status") == "ACTIVE"
            and receipt.get("repair_kind") == "INCIDENT_REPAIR"
        ):
            return "STEWARD_INCIDENT_REPAIR"

    return "BUILDER"


def declaration_paths(repo: Path, changed: list[str]) -> list[Path]:
    paths = []
    for rel in changed:
        if rel.startswith(DECLARATION_PREFIX) and rel.endswith(DECLARATION_SUFFIX):
            path = repo / rel
            require(path.is_file(), f"Builder declaration disappeared: {rel}")
            require(not path.is_symlink(), f"Builder declaration may not be a symlink: {rel}")
            paths.append(path)
    return paths


def ci_receipt(head_sha: str) -> dict[str, Any]:
    conclusion = os.environ.get("N0TE2_CI_CONCLUSION", "").strip().lower()
    run_id = os.environ.get("N0TE2_CI_RUN_ID", "").strip()
    run_url = os.environ.get("N0TE2_CI_RUN_URL", "").strip()
    require(conclusion in {"success", "failure", "cancelled", "timed_out"}, "trusted CI conclusion missing or unsupported")
    result = "PASS" if conclusion == "success" else "FAIL"
    refs = [f"github-actions-run:{run_id}"] if run_id else []
    if run_url:
        refs.append(run_url)
    return {
        "command": "N0TE2 Governance exact-head matrix",
        "environment": "trusted workflow_run observation of candidate CI",
        "exact_head": head_sha,
        "result": result,
        "observed_at": os.environ.get("N0TE2_CI_COMPLETED_AT", "") or "RUNTIME_OBSERVED",
        "artifact_refs": refs,
    }


def qualify(repo: Path, output: Path | None = None) -> dict[str, Any]:
    base_sha = os.environ.get("N0TE2_BASE_SHA", "").strip()
    head_sha = os.environ.get("N0TE2_HEAD_SHA", "").strip()
    pr_number = os.environ.get("N0TE2_PR_NUMBER", "").strip()
    base_branch = os.environ.get("N0TE2_BASE_BRANCH", "main").strip() or "main"
    require(HEX40.fullmatch(base_sha) is not None, "N0TE2_BASE_SHA must be exact SHA40")
    require(HEX40.fullmatch(head_sha) is not None, "N0TE2_HEAD_SHA must be exact SHA40")

    actual = git(repo, "rev-parse", "HEAD")
    require(actual == head_sha, f"candidate checkout mismatch: expected {head_sha}, got {actual}")

    changed = changed_files(repo, base_sha, head_sha)
    role = role_for_candidate(repo, changed)
    declarations = declaration_paths(repo, changed)

    if role != "BUILDER":
        require(len(declarations) <= 1, "Steward candidate may carry at most one Builder declaration")
        result = {
            "schema_version": 1,
            "state": "NOT_APPLICABLE_STEWARD_ROLE",
            "candidate_role": role,
            "head_sha": head_sha,
            "base_sha": base_sha,
            "declaration_paths": [str(p.relative_to(repo)) for p in declarations],
            "authority_effect": "NONE",
        }
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result

    require(
        len(declarations) == 1,
        "ordinary Builder candidate must change exactly one governance/builder_handoffs/*.builder.json declaration",
    )

    trusted = load_trusted_builder_module()
    declaration = trusted.load(declarations[0])
    require(
        declaration.get("kind") == "N0TE_BUILDER_HANDOFF_DECLARATION",
        "candidate handoff file must be an unsealed Builder declaration",
    )

    runtime_declaration = copy.deepcopy(declaration)
    assertions = runtime_declaration.get("builder_assertions")
    require(isinstance(assertions, dict), "Builder declaration lacks builder_assertions")
    validation = assertions.setdefault("validation", {})
    require(isinstance(validation, dict), "Builder validation section must be an object")
    receipt = ci_receipt(head_sha)
    validation["test_receipts"] = [receipt]

    runtime = trusted.collect(
        repo,
        base_sha,
        base_sha,
        base_branch=base_branch,
        pr=pr_number or None,
    )
    manifest = trusted.seal(
        runtime_declaration,
        runtime,
        expected_head=head_sha,
    )
    manifest["authority_verification"]["ci"] = {
        "state": "PASS" if receipt["result"] == "PASS" else "FAIL",
        "evidence_refs": receipt["artifact_refs"],
    }

    if receipt["result"] != "PASS":
        manifest["computed"]["state"] = "HANDOFF_BLOCKED"
        manifest["computed"].setdefault("findings", []).append(
            f"trusted exact-head CI did not pass: {os.environ.get('N0TE2_CI_CONCLUSION', '')}"
        )

    allowed = {"READY_HANDOFF", "SPLIT_RECOMMENDED"}
    require(
        manifest["computed"]["state"] in allowed,
        "Builder handoff qualification failed: "
        + "; ".join(manifest["computed"].get("findings", [])),
    )

    manifest["trusted_intake"] = {
        "candidate_role": role,
        "source_declaration": str(declarations[0].relative_to(repo)),
        "ci_observation_source": "BASE_OWNED_WORKFLOW_RUN",
        "merge_authorization": False,
        "steward_approval": False,
    }

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        result = qualify(Path(args.repo).resolve(), Path(args.output) if args.output else None)
        print(
            "N0TE2 BUILDER HANDOFF: GREEN "
            f"state={result.get('computed', {}).get('state', result.get('state'))} "
            f"role={result.get('trusted_intake', {}).get('candidate_role', result.get('candidate_role'))}"
        )
        return 0
    except BuilderIntakeError as exc:
        print(f"N0TE2 BUILDER HANDOFF: RED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
