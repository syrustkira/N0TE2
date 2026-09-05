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
INCIDENT_REPAIR_NODE = "INCIDENT-REPAIR"
ACTIVE_REPAIR_KIND = "INCIDENT_REPAIR"
CLOSURE_REPAIR_KIND = "INCIDENT_REPAIR_CLOSURE"
TARGET_KINDS = {"GOVERNANCE", "MERGED_PRODUCT"}
META_TOKEN = "MAIN_STEWARD_LABEL"
CLOSURE_PATHS = {
    "governance/active_receipt.json",
    "governance/current_state.json",
    "governance/incidents.jsonl",
}
PROTECTED_CANDIDATE_GOVERNANCE = {
    "governance/build_handoff.py",
    "governance/check_context_lifecycle.py",
    "governance/check_governance.py",
    "governance/check_incident_repair_authority.py",
    "governance/check_steward_integration.py",
    "governance/smoke/consumer_smoke.py",
    "governance/supervision.py",
}


class RepairAuthorityError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RepairAuthorityError(message)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except Exception as exc:
        raise RepairAuthorityError(f"cannot load {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        for lineno, raw in enumerate(path.read_text().splitlines(), 1):
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError(f"line {lineno} is not an object")
            rows.append(value)
    except Exception as exc:
        raise RepairAuthorityError(f"cannot load {path}: {exc}") from exc
    return rows


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT
    ).strip()


def git_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.STDOUT)


def git_json_at(repo: Path, commit_sha: str, path: str) -> dict:
    try:
        raw = git(repo, "show", f"{commit_sha}:{path}")
        value = json.loads(raw)
    except Exception as exc:
        raise RepairAuthorityError(
            f"cannot read trusted-base {path} at {commit_sha}: {exc}"
        ) from exc
    require(isinstance(value, dict), f"trusted-base {path} must be a JSON object")
    return value


def path_exists_at(repo: Path, commit_sha: str, path: str) -> bool:
    try:
        git_bytes(repo, "cat-file", "-e", f"{commit_sha}:{path}")
        return True
    except subprocess.CalledProcessError:
        return False


def changed_paths(repo: Path, base_sha: str) -> list[str]:
    try:
        raw = git_bytes(
            repo,
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            f"{base_sha}...HEAD",
        )
    except subprocess.CalledProcessError as exc:
        raise RepairAuthorityError(
            f"cannot derive candidate paths from {base_sha}: {exc.output.decode(errors='replace')}"
        ) from exc
    return [os.fsdecode(item) for item in raw.split(b"\0") if item]


def current_incident(rows: list[dict], incident_id: str) -> dict | None:
    found = None
    for row in rows:
        if row.get("id") == incident_id:
            found = row
    return found


def normalize_id_list(receipt: dict, field: str) -> list[str]:
    ids = receipt.get(field)
    if ids is None:
        return []
    require(isinstance(ids, list), f"{field} must be a list")
    require(ids, f"{field} must not be empty when present")
    require(
        all(isinstance(item, str) and item.strip() for item in ids),
        f"{field} must contain non-empty text ids",
    )
    normalized = [item.strip() for item in ids]
    require(len(normalized) == len(set(normalized)), f"{field} must be unique")
    return normalized


def normalize_incident_ids(receipt: dict) -> list[str]:
    return normalize_id_list(receipt, "incident_repair_ids")


def normalize_closed_incident_ids(receipt: dict) -> list[str]:
    return normalize_id_list(receipt, "closed_incident_repair_ids")


def validate_meta_protection(repo: Path, base_sha: str) -> None:
    if os.environ.get("N0TE2_EVENT_MODE") != "PR":
        return
    paths = set(changed_paths(repo, base_sha))
    protected = sorted(paths & PROTECTED_CANDIDATE_GOVERNANCE)
    if protected:
        require(
            os.environ.get("N0TE2_META_GOVERNANCE_REOPEN") == META_TOKEN,
            "candidate changed protected ordinary-governance enforcement without explicit meta-governance reopen: "
            + ", ".join(protected),
        )


def validate_incidents_open(repo: Path, incident_ids: list[str]) -> list[dict]:
    rows = load_jsonl(repo / "governance/incidents.jsonl")
    incidents: list[dict] = []
    for incident_id in incident_ids:
        row = current_incident(rows, incident_id)
        require(row is not None, f"repair names unknown incident: {incident_id}")
        status = str(row.get("status") or "").strip().upper()
        require(status.startswith("OPEN"), f"repair names non-open incident: {incident_id}")
        contract = row.get("repair_contract")
        require(isinstance(contract, dict), f"incident {incident_id} lacks repair_contract")
        require(
            contract.get("future_receipt_field") == "incident_repair_ids",
            f"incident {incident_id} does not permit receipt-bound repair",
        )
        incidents.append(row)
    return incidents


def validate_incidents_resolved(
    repo: Path,
    incident_ids: list[str],
    *,
    closed_repair_receipt_id: str,
    closure_receipt_id: str,
) -> None:
    rows = load_jsonl(repo / "governance/incidents.jsonl")
    for incident_id in incident_ids:
        row = current_incident(rows, incident_id)
        require(row is not None, f"repair closure names unknown incident: {incident_id}")
        status = str(row.get("status") or "").strip().upper()
        require(
            status.startswith("RESOLVED"),
            f"repair closure requires durable RESOLVED incident truth: {incident_id} is {status or '<missing>'}",
        )
        require(
            isinstance(row.get("resolved_at"), str) and row["resolved_at"].strip(),
            f"repair closure resolution event lacks resolved_at: {incident_id}",
        )
        require(
            isinstance(row.get("resolution_condition"), str) and row["resolution_condition"].strip(),
            f"repair closure resolution event lacks resolution_condition: {incident_id}",
        )
        evidence = row.get("evidence")
        require(
            isinstance(evidence, dict) and evidence,
            f"repair closure resolution event lacks evidence: {incident_id}",
        )
        require(
            row.get("repair_receipt_id") == closed_repair_receipt_id,
            f"repair closure resolution event is not bound to exact ACTIVE repair receipt: {incident_id}",
        )
        require(
            row.get("closure_receipt_id") == closure_receipt_id,
            f"repair closure resolution event is not bound to exact terminal closure receipt: {incident_id}",
        )


def validate_active_repair(
    repo: Path,
    base_sha: str,
    current: dict,
    receipt: dict,
    incident_ids: list[str],
) -> None:
    require(current.get("lifecycle_state") == "ACTIVE", "incident repair authority requires ACTIVE lifecycle")
    require(receipt.get("status") == "ACTIVE", "incident repair authority requires ACTIVE receipt")
    require(not receipt.get("closed_incident_repair_ids"), "active repair receipt cannot carry terminal closure incident history")
    require(receipt.get("node_id") == current.get("active_node"), "incident repair receipt node is stale")
    require(receipt.get("increment_id") == current.get("active_increment"), "incident repair receipt increment is stale")
    require(receipt.get("baseline_sha") == base_sha, "incident repair receipt must bind exact candidate base")
    require(receipt.get("repair_kind") == ACTIVE_REPAIR_KIND, "incident repair authority requires repair_kind=INCIDENT_REPAIR")
    require(current.get("active_node") == INCIDENT_REPAIR_NODE, "first-class incident repair must use active_node=INCIDENT-REPAIR")
    increment = current.get("active_increment")
    require(isinstance(increment, str) and increment.startswith("INCIDENT-REPAIR-"), "incident repair increment is not bounded to INCIDENT-REPAIR")
    require(type(current.get("product_code_authorized")) is bool, "incident repair product authority must be a JSON boolean")
    require(receipt.get("product_code_allowed") == current.get("product_code_authorized"), "incident repair product authority mismatch")
    target_kind = receipt.get("repair_target_kind")
    require(target_kind in TARGET_KINDS, f"unsupported incident repair target kind: {target_kind}")
    repair_issue = receipt.get("repair_issue")
    require(type(repair_issue) is int and repair_issue > 0, "incident repair requires a positive integer repair_issue")

    incidents = validate_incidents_open(repo, incident_ids)
    paths = changed_paths(repo, base_sha)
    allowed_exact = receipt.get("allowed_exact_paths", [])
    allowed_prefixes = receipt.get("allowed_prefixes", [])
    require(isinstance(allowed_exact, list) and isinstance(allowed_prefixes, list), "repair path bounds must be lists")
    unauthorized = [
        path
        for path in paths
        if path not in allowed_exact
        and not any(isinstance(prefix, str) and path.startswith(prefix) for prefix in allowed_prefixes)
    ]
    require(not unauthorized, "incident repair changed paths outside exact receipt: " + ", ".join(unauthorized))

    if target_kind == "GOVERNANCE":
        require(not allowed_prefixes, "GOVERNANCE repair must enumerate exact allowed paths; broad prefixes are not repair authority")
        require(set(allowed_exact) == set(paths), "GOVERNANCE repair allowed_exact_paths must exactly equal changed paths")
        require(receipt.get("product_code_allowed") is False, "GOVERNANCE repair cannot authorize product code")
        bad = [
            path
            for path in paths
            if not (
                path.startswith("governance/")
                or path.startswith("tests/governance/")
                or path == ".github/workflows/steward-integration.yml"
            )
        ]
        require(not bad, "GOVERNANCE repair changed non-governance path: " + ", ".join(bad))
        return

    require(receipt.get("product_code_allowed") is True, "MERGED_PRODUCT repair must authorize bounded product code")
    target_sha = receipt.get("repair_target_merge_sha")
    require(isinstance(target_sha, str) and HEX40.fullmatch(target_sha), "MERGED_PRODUCT repair requires exact repair_target_merge_sha")
    for incident in incidents:
        discovery_sha = incident.get("evidence", {}).get("main_at_discovery")
        require(isinstance(discovery_sha, str) and HEX40.fullmatch(discovery_sha), f"incident {incident.get('id')} lacks exact main_at_discovery")
        try:
            git(repo, "merge-base", "--is-ancestor", target_sha, discovery_sha)
        except subprocess.CalledProcessError as exc:
            raise RepairAuthorityError(
                f"repair target {target_sha} does not predate incident discovery main {discovery_sha}"
            ) from exc
    construction_paths = [
        path
        for path in paths
        if path.startswith("n0te2/")
        or (path.startswith("tests/") and not path.startswith("tests/governance/"))
        or path in {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"}
    ]
    broad_product_paths = [path for path in construction_paths if path not in set(allowed_exact)]
    require(
        not broad_product_paths,
        "MERGED_PRODUCT repair must name every construction path in allowed_exact_paths: "
        + ", ".join(broad_product_paths),
    )
    new_paths = [path for path in construction_paths if not path_exists_at(repo, target_sha, path)]
    require(
        not new_paths,
        "MERGED_PRODUCT repair introduced construction paths absent from the target merge: "
        + ", ".join(new_paths),
    )
    require(not allowed_prefixes, "MERGED_PRODUCT repair must enumerate exact allowed paths; broad prefixes are not repair authority")
    require(set(allowed_exact) == set(paths), "MERGED_PRODUCT repair allowed_exact_paths must exactly equal changed paths")


def validate_closure(
    repo: Path,
    base_sha: str,
    current: dict,
    receipt: dict,
    incident_ids: list[str],
) -> None:
    require(current.get("lifecycle_state") in {"STABLE", "WAITING", "BLOCKED"}, "repair closure must return to a terminal lifecycle")
    require(current.get("active_node") is None and current.get("active_increment") is None, "repair closure cannot retain active construction")
    require(current.get("product_code_authorized") is False, "repair closure cannot authorize product code")
    require(receipt.get("status") == "INACTIVE", "repair closure requires INACTIVE receipt")
    require(not receipt.get("incident_repair_ids"), "repair closure cannot retain active incident repair authority")
    require(receipt.get("product_code_allowed") is False, "repair closure receipt cannot authorize product code")
    require(receipt.get("legacy_admission_allowed") is False, "repair closure receipt cannot authorize legacy admission")
    require(receipt.get("repair_kind") == CLOSURE_REPAIR_KIND, "terminal closure history requires repair_kind=INCIDENT_REPAIR_CLOSURE")
    require(receipt.get("baseline_sha") == base_sha, "repair closure receipt must bind exact active-repair base")
    closed_receipt_id = receipt.get("closed_repair_receipt_id")
    require(isinstance(closed_receipt_id, str) and closed_receipt_id.strip(), "repair closure requires closed_repair_receipt_id")
    closure_receipt_id = receipt.get("receipt_id")
    require(isinstance(closure_receipt_id, str) and closure_receipt_id.strip(), "repair closure requires a stable terminal receipt_id")
    require(closure_receipt_id != closed_receipt_id, "repair closure receipt_id must differ from the ACTIVE repair receipt being closed")
    require(set(receipt.get("allowed_exact_paths", [])) == CLOSURE_PATHS, "repair closure must bind exactly the three durable closure paths")
    require(receipt.get("allowed_prefixes", []) == [], "repair closure cannot authorize path prefixes")

    base_current = git_json_at(repo, base_sha, "governance/current_state.json")
    base_receipt = git_json_at(repo, base_sha, "governance/active_receipt.json")
    require(base_current.get("lifecycle_state") == "ACTIVE", "repair closure base was not ACTIVE")
    require(base_current.get("active_node") == INCIDENT_REPAIR_NODE, "repair closure base was not first-class INCIDENT-REPAIR")
    require(base_receipt.get("status") == "ACTIVE", "repair closure base receipt was not ACTIVE")
    require(base_receipt.get("repair_kind") == ACTIVE_REPAIR_KIND, "repair closure base receipt was not an incident repair")
    require(base_receipt.get("receipt_id") == closed_receipt_id, "repair closure does not name exact base receipt")
    base_ids = normalize_incident_ids(base_receipt)
    require(base_ids == incident_ids, "repair closure incident ids differ from exact base repair")
    require(base_receipt.get("node_id") == base_current.get("active_node"), "repair closure base receipt node was stale")
    require(base_receipt.get("increment_id") == base_current.get("active_increment"), "repair closure base receipt increment was stale")

    validate_incidents_resolved(
        repo,
        incident_ids,
        closed_repair_receipt_id=closed_receipt_id,
        closure_receipt_id=closure_receipt_id,
    )

    paths = set(changed_paths(repo, base_sha))
    require(CLOSURE_PATHS.issubset(paths), "repair closure must change current_state, active_receipt and incidents")
    bad = sorted(paths - CLOSURE_PATHS)
    require(not bad, "repair closure changed paths outside its exact terminal surfaces: " + ", ".join(bad))


def run(repo: Path) -> None:
    base_sha = str(os.environ.get("N0TE2_BASE_SHA") or "").strip().lower()
    require(HEX40.fullmatch(base_sha) is not None, "N0TE2_BASE_SHA must be exact lowercase SHA")
    current = load_json(repo / "governance/current_state.json")
    receipt = load_json(repo / "governance/active_receipt.json")
    active_ids = normalize_incident_ids(receipt)
    closed_ids = normalize_closed_incident_ids(receipt)

    validate_meta_protection(repo, base_sha)

    active_repair_signaled = (
        current.get("active_node") == INCIDENT_REPAIR_NODE
        or receipt.get("repair_kind") == ACTIVE_REPAIR_KIND
        or bool(active_ids)
    )
    if active_repair_signaled:
        require(receipt.get("status") == "ACTIVE", "INACTIVE receipt cannot carry active incident_repair_ids")
        require(active_ids, "ACTIVE incident repair requires incident_repair_ids")
        validate_active_repair(repo, base_sha, current, receipt, active_ids)
        print(
            "N0TE2 INCIDENT REPAIR AUTHORITY: GREEN "
            f"mode=ACTIVE target={receipt.get('repair_target_kind')} incidents={','.join(active_ids)}"
        )
        return

    closure_signaled = receipt.get("repair_kind") == CLOSURE_REPAIR_KIND or bool(closed_ids)
    if closure_signaled:
        require(closed_ids, "INCIDENT_REPAIR_CLOSURE requires closed_incident_repair_ids")
        validate_closure(repo, base_sha, current, receipt, closed_ids)
        print(
            "N0TE2 INCIDENT REPAIR AUTHORITY: GREEN "
            f"mode=CLOSURE incidents={','.join(closed_ids)}"
        )
        return

    require(not active_ids, "incident_repair_ids cannot exist outside active incident repair")
    require(not closed_ids, "closed incident history cannot exist outside INCIDENT_REPAIR_CLOSURE")
    require(receipt.get("repair_kind") not in {ACTIVE_REPAIR_KIND, CLOSURE_REPAIR_KIND}, "repair_kind cannot exist without matching repair metadata")
    print("N0TE2 INCIDENT REPAIR AUTHORITY: GREEN mode=NONE")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    try:
        run(Path(args.repo).resolve())
        return 0
    except (RepairAuthorityError, subprocess.CalledProcessError) as exc:
        print(f"N0TE2 INCIDENT REPAIR AUTHORITY: RED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
