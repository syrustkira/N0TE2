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


def build_runtime_handoff(repo: Path) -> dict:
    handoff = load_json(repo / "governance/handoff.json")
    current = load_json(repo / "governance/current_state.json")
    require(handoff.get("repository") == current.get("repository") == "syrustkira/N0TE2", "handoff/current repository mismatch")

    head = git(repo, "rev-parse", "HEAD")
    expected = os.environ.get("N0TE2_HEAD_SHA") or os.environ.get("EVIDENCE_SHA")
    if expected:
        require(head == expected, f"exact-head mismatch: expected {expected}, got {head}")

    lifecycle = handoff["lifecycle"]
    require(lifecycle["state"] == current.get("lifecycle_state"), "handoff lifecycle is stale")
    require(lifecycle.get("active_node") == current.get("active_node"), "handoff active node is stale")
    require(lifecycle.get("active_increment") == current.get("active_increment"), "handoff active increment is stale")

    refs = handoff["reconstruction"]["required_refs"]
    missing = [rel for rel in refs if not (repo / rel).exists()]
    require(not missing, f"handoff references missing authority: {', '.join(missing)}")

    runtime = {
        "schema_version": 1,
        "repository": handoff["repository"],
        "observed_head_sha": head,
        "head_binding": "RUNTIME_EXACT",
        "delivery": handoff["delivery"],
        "lifecycle": lifecycle,
        "controller": handoff["controller"],
        "open_incidents": handoff.get("open_incidents", []),
        "next_admissible_action": handoff["next_admissible_action"],
        "required_refs": refs,
        "archaeology_fallback": handoff["reconstruction"]["archaeology_fallback"],
    }
    return runtime


def build_observation(runtime: dict) -> dict:
    return {
        "schema_version": 1,
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
                f"active={runtime['lifecycle'].get('active_node') or '-'}"
            )
        elif not args.output and not args.observation_output:
            print(json.dumps(runtime, indent=2, sort_keys=True))
        return 0
    except (HandoffError, subprocess.CalledProcessError) as exc:
        print(f"N0TE2 HANDOFF: RED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
