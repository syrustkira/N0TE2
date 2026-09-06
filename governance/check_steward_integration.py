#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HEX40 = re.compile(r"^[0-9a-f]{40}$")
VALID_LIFECYCLE_STATES = {"ACTIVE", "STABLE", "WAITING", "BLOCKED"}
TERMINAL_LIFECYCLE_STATES = {"STABLE", "WAITING", "BLOCKED"}
VALID_INCIDENT_STATUS_PREFIXES = ("OPEN", "RESOLVED")
ZERO_SHA = "0" * 40
PRIVILEGED_WORKFLOW_PREFIX = ".github/workflows/"

CONSTRUCTION_SENSITIVE_EXACT = {
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
}

REQUIRED_PLATFORM_CONTEXTS = [
    "n0te2-governance-Linux",
    "n0te2-governance-Windows",
    "n0te2-governance-macOS",
]

TRUSTED_GATE_ARTIFACTS = [
    "governance/check_steward_integration.py",
    "governance/merge_policy.json",
    "governance/automation_registry.json",
    ".github/workflows/steward-integration.yml",
    ".github/workflows/governance.yml",
]

CRITICAL_CANDIDATE_INPUTS = [
    "governance/requirements.json",
    "governance/completion_graph.json",
    "governance/current_state.json",
    "governance/active_receipt.json",
    "governance/incidents.jsonl",
    "governance/handoff.json",
    "governance/merge_policy.json",
    "governance/automation_registry.json",
    *TRUSTED_GATE_ARTIFACTS,
]

CANONICAL_SOURCE = "N0TE_PRODUCT_DB/SCOPE_LEDGER"
CANONICAL_SOURCE_REVISION = "524"
CANONICAL_SCOPE_ROW = {
    "source": CANONICAL_SOURCE,
    "start": 2,
    "end": 170,
    "retained_requirement_count": 169,
    "source_revision": CANONICAL_SOURCE_REVISION,
    "rule": (
        "The canonical product ledger owns full accepted N0TE scope. "
        "The temporary build graph is a construction-selection index and must not expand "
        "into a second product database merely because canonical scope grows. "
        "Canonical extensions preserve accepted scope without selecting work by themselves."
    ),
}
REQUIREMENTS_TOP_LEVEL_KEYS = {
    "schema_version",
    "sequence",
    "sequence_role",
    "canonical_scope",
    "canonical_extensions",
    "held_or_boundary",
    "superseded",
    "default_classification",
    "known_blocks_candidate",
    "known_selects_work",
    "non_active_blocks_candidate",
    "selection_contract",
}

CANONICAL_EXTENSION_ROWS = [
    {"id":"REQ-SCOPE-154","state":"MAPPED","selected":False,"construction_affinity":["UX-01","CORE-02","CORE-03","CORE-04"],"summary":"Creative Partner professional-lens selection and bounded multi-perspective synthesis."},
    {"id":"REQ-SCOPE-155","state":"MAPPED","selected":False,"construction_affinity":["UX-01","CORE-04"],"summary":"Creative Partner presence, initiative and silence policy including explicit no-action posture."},
    {"id":"REQ-SCOPE-156","state":"MAPPED","selected":False,"construction_affinity":["UX-01","CORE-01","CORE-02"],"summary":"Relevance brokerage and lens-sensitive bounded context projection."},
    {"id":"REQ-SCOPE-157","state":"MAPPED","selected":False,"construction_affinity":["UX-01","CORE-01","CORE-02","CORE-04"],"summary":"Versioned backward-readable Creative Partner InteractionPolicy with ephemeral-first lifecycle and stale-state safety."},
    {"id":"REQ-SCOPE-158","state":"MAPPED","selected":False,"construction_affinity":["UX-01","CORE-02"],"summary":"Creative tension, tradeoff and Challenger reasoning with simulated-versus-observed audience truth."},
    {"id":"REQ-SCOPE-159","state":"MAPPED","selected":False,"construction_affinity":["CONV-01","CORE-01","CORE-02","CORE-04"],"summary":"Stable semantic keys and versioned definition-lineage evolution."},
    {"id":"REQ-SCOPE-160","state":"MAPPED","selected":False,"construction_affinity":["DAW-07","CORE-03","CORE-04","CONV-01"],"summary":"Provider/host/protocol-neutral negotiated capability with versioned adapters, fidelity truth and route receipts."},
    {"id":"REQ-SCOPE-161","state":"MAPPED","selected":False,"construction_affinity":["ART-01","OPS-04","OPS-06","CORE-02"],"summary":"Canonical Music Professional Master Map role ontology across the music value network."},
    {"id":"REQ-SCOPE-162","state":"MAPPED","selected":False,"construction_affinity":["OPS-05","CORE-04","CONV-01"],"summary":"Occupational music safety across role-specific hearing, physical, service and operational risks."},
    {"id":"REQ-SCOPE-163","state":"MAPPED","selected":False,"construction_affinity":["OPS-04","OPS-06","CORE-02"],"summary":"Professional portfolio, credits, reputation and referral evidence."},
    {"id":"REQ-SCOPE-164","state":"MAPPED","selected":False,"construction_affinity":["OPS-06","CORE-02"],"summary":"Role-specific economics for music-professional jobs and career decisions."},
    {"id":"REQ-SCOPE-165","state":"MAPPED","selected":False,"construction_affinity":["OPS-04","CORE-04"],"summary":"Cross-role handoff contracts that preserve responsibility, artifacts, authority and next-owner clarity."},
    {"id":"REQ-SCOPE-166","state":"MAPPED","selected":False,"construction_affinity":["CORE-04","OPS-04","OPS-05"],"summary":"Professional ethics and duty-of-care boundaries for role-aware work."},
    {"id":"REQ-SCOPE-167","state":"MAPPED","selected":False,"construction_affinity":["OPS-06","CORE-02","ART-01"],"summary":"Role development, skill progression and transitions across music careers."},
    {"id":"REQ-SCOPE-168","state":"MAPPED","selected":False,"construction_affinity":["APP-01","PLATFORM-00","CORE-04","OPS-05"],"summary":"Physical and service resilience for professional music workflows."},
    {"id":"REQ-SCOPE-169","state":"MAPPED","selected":False,"construction_affinity":["OPS-04","OPS-02","CONV-01"],"summary":"Territory, international and cultural context for professional music work."},
    {"id":"REQ-SCOPE-170","state":"MAPPED","selected":False,"construction_affinity":["LATER-01","OPS-02","OPS-06"],"summary":"Legacy, catalog and career succession continuity; accepted but dependency-gated/later."},
]

CANONICAL_SELECTION_FIELDS = {
    "held_or_boundary": [
        "REQ-SCOPE-044","REQ-SCOPE-045","REQ-SCOPE-047","REQ-SCOPE-062",
        "REQ-SCOPE-063","REQ-SCOPE-064","REQ-SCOPE-065","REQ-SCOPE-066",
        "REQ-SCOPE-108",
    ],
    "superseded": ["REQ-SCOPE-046"],
    "default_classification": "KNOWN",
    "known_blocks_candidate": True,
    "known_selects_work": False,
    "non_active_blocks_candidate": False,
    "selection_contract": (
        "A requirement may be known and still unfinished without being selected. "
        "Work becomes ACTIVE only through current_state plus an active receipt after global "
        "dependency-ready selection. Canonical extensions remain retained even when the "
        "build graph does not yet index them as construction nodes."
    ),
}

HANDOFF_TOP_LEVEL_KEYS = {
    "schema_version",
    "repository",
    "delivery",
    "head_binding",
    "controller",
    "lifecycle",
    "derived_runtime_truth",
    "reconstruction",
}
HANDOFF_COMPATIBILITY_KEYS = {"state", "active_node", "active_increment"}
HANDOFF_RUNTIME_TRUTH_CONTRACT = {
    "lifecycle_source": "governance/current_state.json",
    "next_admissible_action_source": "governance/current_state.json",
    "canonical_scope_source": "governance/requirements.json",
    "open_incidents_source": "governance/incidents.jsonl",
    "delivery_state_source": "GITHUB_RUNTIME",
    "executor_liveness_sources": ["GITHUB_ACTIONS_RUNTIME", "CHATGPT_AUTOMATIONS_RUNTIME"],
    "legacy_lifecycle_copy_policy": (
        "The empty lifecycle object carries no canonical state. It exists only as a "
        "compatibility/adversarial hook: any supplied legacy lifecycle fields must match "
        "current_state or validation fails."
    ),
    "rule": (
        "Handoff is a reconstruction recipe, not the owner of mutable runtime truth. "
        "Lifecycle, delivery state, executor liveness, next action, canonical scope and open "
        "incidents are derived from their canonical owners."
    ),
}

MERGE_POLICY_TOP_LEVEL_KEYS = {
    "schema_version",
    "target_branch",
    "required_exact_head_status_contexts",
    "requirements",
    "steward_gate",
    "external_enforcement",
}
MERGE_REQUIREMENT_KEYS = {
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
}
EXTERNAL_ENFORCEMENT_EXPECTED = {
    "desired": (
        "After Steward-gate bootstrap, GitHub branch protection or a repository ruleset should "
        "require the three exact-head platform check contexts plus the trusted "
        "n0te2-steward-structure context where administratively available."
    ),
    "repository_file_cannot_prevent_direct_push_by_itself": True,
    "current_truth": (
        "Repository policy and trusted structural CI define evidence requirements, while the "
        "single active Main Steward remains the live merge-authorization owner unless external "
        "branch protection or ruleset evidence proves equivalent atomic enforcement. A structural "
        "status, including green, is not a merge receipt."
    ),
}

REGISTRY_TOP_LEVEL_KEYS = {"schema_version", "supervisor", "runtime_state_contract", "actors"}
STEWARD_ACTOR_TOP_LEVEL_KEYS = {
    "id",
    "kind",
    "role_class",
    "path",
    "parent",
    "reports_to",
    "purpose",
    "boundary",
    "reason_running",
    "mode",
    "authority",
    "allowed_mutations",
    "wake_condition",
    "retirement_condition",
    "failure_policy",
    "escalation_target",
    "auto_spawn_successor",
    "lifecycle",
    "runtime_state_source",
    "observability",
}

BLOCKING_REPAIR_SCOPE = "BLOCKING_EXCEPT_EXPLICIT_INCIDENT_REPAIR"
BOOTSTRAP_PR_NUMBER = "211"
META_GOVERNANCE_REOPEN_ENV = "N0TE2_META_GOVERNANCE_REOPEN"
META_GOVERNANCE_REOPEN_VALUE = "MAIN_STEWARD_LABEL"
META_GOVERNANCE_REOPEN_LABEL = "steward-meta-governance-reopen"


class StewardIntegrationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise StewardIntegrationError(message)


def _typed_json(value: Any) -> Any:
    if value is None:
        return ("null",)
    if type(value) is bool:
        return ("bool", value)
    if type(value) is int:
        return ("int", value)
    if type(value) is float:
        require(math.isfinite(value), "non-finite JSON number is not canonical")
        return ("float", repr(value))
    if type(value) is str:
        return ("str", value)
    if type(value) is list:
        return ("list", tuple(_typed_json(item) for item in value))
    if type(value) is dict:
        return ("dict", tuple((key, _typed_json(value[key])) for key in sorted(value)))
    raise StewardIntegrationError(f"unsupported JSON value type: {type(value).__name__}")


def exact_json_equal(left: Any, right: Any) -> bool:
    return _typed_json(left) == _typed_json(right)


def _require_regular_file(path: Path) -> None:
    require(path.exists(), f"required candidate file is missing: {path}")
    require(not path.is_symlink(), f"candidate governance input must not be a symlink: {path}")
    require(path.is_file(), f"candidate governance input must be a regular file: {path}")


def _require_repo_path_without_symlink_ancestors(repo: Path, relative: str) -> None:
    cursor = repo
    parts = Path(relative).parts
    require(parts, f"candidate governance path is empty: {relative!r}")
    for index, part in enumerate(parts):
        cursor = cursor / part
        require(
            not cursor.is_symlink(),
            f"candidate governance path component must not be a symlink: {relative} at {cursor}",
        )
        if index < len(parts) - 1:
            require(
                cursor.exists() and cursor.is_dir(),
                f"candidate governance ancestor is missing or not a directory: {cursor}",
            )
    _require_regular_file(cursor)


def load_json(path: Path) -> dict:
    _require_regular_file(path)
    try:
        value = json.loads(path.read_text())
    except Exception as exc:
        raise StewardIntegrationError(f"cannot load {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


def load_jsonl(path: Path) -> list[dict]:
    _require_regular_file(path)
    rows: list[dict] = []
    try:
        for line_number, raw in enumerate(path.read_text().splitlines(), 1):
            if not raw.strip():
                continue
            value = json.loads(raw)
            require(isinstance(value, dict), f"{path} line {line_number} must contain a JSON object")
            rows.append(value)
    except StewardIntegrationError:
        raise
    except Exception as exc:
        raise StewardIntegrationError(f"cannot load {path}: {exc}") from exc
    return rows


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT
    ).strip()


def git_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.STDOUT)


def git_show_bytes(repo: Path, ref: str, path: str) -> bytes:
    try:
        return git_bytes(repo, "show", f"{ref}:{path}")
    except subprocess.CalledProcessError as exc:
        raise StewardIntegrationError(
            f"cannot load {path} from base {ref}: {exc.output.decode(errors='replace')}"
        ) from exc


def git_json(repo: Path, ref: str, path: str) -> dict:
    try:
        value = json.loads(git_show_bytes(repo, ref, path).decode())
    except StewardIntegrationError:
        raise
    except Exception as exc:
        raise StewardIntegrationError(f"cannot parse {path} from base {ref}: {exc}") from exc
    require(isinstance(value, dict), f"{path} at base {ref} must contain a JSON object")
    return value


def git_jsonl(repo: Path, ref: str, path: str) -> list[dict]:
    try:
        text = git_show_bytes(repo, ref, path).decode()
        rows = [json.loads(raw) for raw in text.splitlines() if raw.strip()]
    except StewardIntegrationError:
        raise
    except Exception as exc:
        raise StewardIntegrationError(f"cannot parse {path} from base {ref}: {exc}") from exc
    require(all(isinstance(row, dict) for row in rows), f"{path} at base {ref} must be JSON objects")
    return rows


def check_candidate_inputs_are_regular(repo: Path) -> None:
    for relative in CRITICAL_CANDIDATE_INPUTS:
        _require_repo_path_without_symlink_ancestors(repo, relative)


def check_canonical_extensions(repo: Path) -> None:
    doc = load_json(repo / "governance/requirements.json")
    graph_doc = load_json(repo / "governance/completion_graph.json")
    graph_ids = {
        row.get("id")
        for row in graph_doc.get("nodes", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }

    require(
        set(doc.keys()) == REQUIREMENTS_TOP_LEVEL_KEYS,
        "requirements authority surface changed or gained unreviewed shadow fields",
    )
    require(doc.get("schema_version") == 5, "requirements schema changed without Steward manifest review")
    require(doc.get("sequence_role") == "BUILD_GRAPH_INDEX", "requirement sequence role changed")
    require(
        exact_json_equal(doc.get("sequence"), {"start": 2, "end": 153}),
        "build-graph requirement index changed unexpectedly",
    )
    canonical = doc.get("canonical_scope", {})
    require(canonical.get("source") == CANONICAL_SOURCE, "canonical requirement source changed")
    require(
        canonical.get("source_revision") == CANONICAL_SOURCE_REVISION,
        "canonical requirement source revision changed without a reviewed Steward manifest update",
    )
    require(
        canonical.get("start") == 2 and canonical.get("end") == 170,
        "canonical retained scope changed without a reviewed Steward manifest update",
    )
    require(
        canonical.get("retained_requirement_count") == 169,
        "canonical retained requirement count is inconsistent",
    )
    require(
        exact_json_equal(canonical, CANONICAL_SCOPE_ROW),
        "canonical retained scope contract gained unreviewed fields or semantics",
    )

    extensions = doc.get("canonical_extensions")
    require(isinstance(extensions, list), "canonical_extensions must be a list")
    require(
        exact_json_equal(extensions, CANONICAL_EXTENSION_ROWS),
        "canonical extension semantics, JSON types, or construction affinities changed without a reviewed Steward manifest update",
    )
    for row in extensions:
        require(row.get("selected") is False, f"{row.get('id')} selected must remain the JSON boolean false")
        require(
            all(node_id in graph_ids for node_id in row["construction_affinity"]),
            f"{row['id']} references an unknown construction-affinity node",
        )

    for key, expected in CANONICAL_SELECTION_FIELDS.items():
        require(
            exact_json_equal(doc.get(key), expected),
            f"canonical requirement selection contract changed without reviewed Steward scope authority: {key}",
        )


def _valid_directory_prefix(prefix: object) -> bool:
    if not isinstance(prefix, str) or not prefix:
        return False
    if prefix != prefix.strip() or "\\" in prefix or prefix.startswith("/"):
        return False
    if not prefix.endswith("/"):
        return False
    components = prefix[:-1].split("/")
    return bool(components) and all(component not in {"", ".", ".."} for component in components)


def _valid_relative_path(path: object) -> bool:
    if not isinstance(path, str) or not path or path != path.strip():
        return False
    if "\\" in path or path.startswith("/") or path.endswith("/"):
        return False
    components = path.split("/")
    return all(component not in {"", ".", ".."} for component in components)


def check_receipt_path_boundaries(repo: Path) -> None:
    receipt = load_json(repo / "governance/active_receipt.json")
    prefixes = receipt.get("allowed_prefixes", [])
    exact_paths = receipt.get("allowed_exact_paths", [])
    require(isinstance(prefixes, list), "active receipt allowed_prefixes must be a list")
    require(isinstance(exact_paths, list), "active receipt allowed_exact_paths must be a list")
    invalid_prefixes = [prefix for prefix in prefixes if not _valid_directory_prefix(prefix)]
    invalid_exact = [path for path in exact_paths if not _valid_relative_path(path)]
    require(
        not invalid_prefixes,
        "active receipt contains unsafe allowed_prefixes; use normalized directory "
        f"boundaries ending in '/': {invalid_prefixes}",
    )
    require(not invalid_exact, f"active receipt contains unsafe allowed_exact_paths: {invalid_exact}")


def _normalized_status(value: object) -> str:
    return value.strip().upper() if isinstance(value, str) else ""


def _validate_incident_status(incident: dict) -> str:
    status = _normalized_status(incident.get("status"))
    require(status, f"incident {incident.get('id', '<unknown>')} has no durable status")
    require(
        status.startswith(VALID_INCIDENT_STATUS_PREFIXES),
        f"incident {incident.get('id', '<unknown>')} has unrecognized status {status!r}; incident truth must be OPEN... or RESOLVED...",
    )
    return status


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
            changed = git_bytes(
                repo,
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--no-renames",
                "--name-only",
                "-z",
                "-r",
                "HEAD",
            )
        except subprocess.CalledProcessError as exc:
            raise StewardIntegrationError(
                f"cannot derive full root candidate diff: {exc.output.decode(errors='replace')}"
            ) from exc
    else:
        mode = str(os.environ.get("N0TE2_DIFF_MODE") or "PR_MERGE_BASE").strip().upper()
        if mode == "EXACT_TREE":
            diff_range = (base, "HEAD")
        else:
            require(mode == "PR_MERGE_BASE", f"unknown N0TE2_DIFF_MODE: {mode}")
            diff_range = (f"{base}...HEAD",)
        try:
            changed = git_bytes(repo, "diff", "--no-renames", "--name-only", "-z", *diff_range)
        except subprocess.CalledProcessError as exc:
            raise StewardIntegrationError(
                f"cannot derive candidate diff from base {base}: {exc.output.decode(errors='replace')}"
            ) from exc
    return [os.fsdecode(raw) for raw in changed.split(b"\0") if raw]


def construction_sensitive(path: str) -> bool:
    folded = path.casefold()
    if folded.startswith("n0te2/"):
        return True
    if folded.startswith("tests/") and not folded.startswith("tests/governance/"):
        return True
    return folded in {item.casefold() for item in CONSTRUCTION_SENSITIVE_EXACT}


def _path_allowed_by_receipt(receipt: dict, path: str) -> bool:
    exact_paths = receipt.get("allowed_exact_paths", [])
    prefixes = receipt.get("allowed_prefixes", [])
    if path in exact_paths:
        return True
    return any(isinstance(prefix, str) and path.startswith(prefix) for prefix in prefixes)


def check_lifecycle_and_active_receipt(repo: Path, verify_git: bool) -> None:
    current = load_json(repo / "governance/current_state.json")
    receipt = load_json(repo / "governance/active_receipt.json")
    lifecycle = current.get("lifecycle_state")

    require(lifecycle in VALID_LIFECYCLE_STATES, f"unrecognized lifecycle_state: {lifecycle!r}")
    require(type(current.get("product_code_authorized")) is bool, "current_state product_code_authorized must be a JSON boolean")
    require(type(receipt.get("product_code_allowed")) is bool, "active receipt product_code_allowed must be a JSON boolean")

    if lifecycle == "ACTIVE":
        require(isinstance(current.get("active_node"), str) and current["active_node"].strip(), "ACTIVE lifecycle requires active_node")
        require(isinstance(current.get("active_increment"), str) and current["active_increment"].strip(), "ACTIVE lifecycle requires active_increment")
        require(receipt.get("status") == "ACTIVE", "ACTIVE lifecycle requires an ACTIVE bounded receipt")
        require(receipt.get("node_id") == current.get("active_node"), "ACTIVE receipt node does not match current_state")
        require(receipt.get("increment_id") == current.get("active_increment"), "ACTIVE receipt increment does not match current_state")
        require(receipt.get("product_code_allowed") == current.get("product_code_authorized"), "ACTIVE receipt/current_state product authority mismatch")
        if verify_git:
            base = candidate_base(repo)
            if base is not None:
                require(receipt.get("baseline_sha") == base, "ACTIVE receipt baseline_sha must bind the exact candidate base")
            changed = candidate_changed_paths(repo)
            construction = [path for path in changed if construction_sensitive(path)]
            if construction:
                require(receipt.get("product_code_allowed") is True, "construction-sensitive ACTIVE repair lacks product-code authority")
            unauthorized = [path for path in construction if not _path_allowed_by_receipt(receipt, path)]
            require(
                not unauthorized,
                "ACTIVE candidate changed construction-sensitive paths outside its bounded receipt: "
                + ", ".join(unauthorized),
            )
    else:
        require(current.get("active_node") is None, f"{lifecycle} cannot retain active_node")
        require(current.get("active_increment") is None, f"{lifecycle} cannot retain active_increment")
        require(current.get("product_code_authorized") is False, f"{lifecycle} cannot authorize product construction")
        require(receipt.get("status") == "INACTIVE", f"{lifecycle} requires an INACTIVE receipt")
        require(receipt.get("product_code_allowed") is False, f"{lifecycle} receipt cannot authorize product code")
        require(receipt.get("legacy_admission_allowed") is False, f"{lifecycle} receipt cannot authorize legacy admission")


def check_incident_history(repo: Path, verify_git: bool) -> list[dict]:
    incidents = load_jsonl(repo / "governance/incidents.jsonl")
    for incident in incidents:
        _validate_incident_status(incident)
    if not verify_git:
        return incidents
    base = candidate_base(repo)
    if base is None:
        return incidents
    base_rows = git_jsonl(repo, base, "governance/incidents.jsonl")
    for incident in base_rows:
        _validate_incident_status(incident)
    require(len(incidents) >= len(base_rows), "candidate removed durable incident history")
    for index, base_row in enumerate(base_rows):
        require(
            exact_json_equal(incidents[index], base_row),
            f"candidate mutated durable incident history at row {index + 1}; append a new event instead",
        )
    return incidents


def _current_incident_rows(rows: list[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        incident_id = row.get("id")
        require(isinstance(incident_id, str) and incident_id.strip(), "incident row lacks a durable id")
        if incident_id not in latest:
            order.append(incident_id)
        latest[incident_id] = row
    return [latest[incident_id] for incident_id in order]


def _explicit_incident_repair(repo: Path, incident: dict) -> bool:
    incident_id = incident["id"]
    contract = incident.get("repair_contract", {})
    require(isinstance(contract, dict), f"blocking incident {incident_id} lacks repair_contract")
    require(
        contract.get("future_receipt_field") == "incident_repair_ids",
        f"blocking incident {incident_id} has an unknown repair receipt contract",
    )
    pr_number = str(os.environ.get("N0TE2_PR_NUMBER") or "").strip()
    bootstrap_pr = str(contract.get("bootstrap_pr") or "").strip()
    if bootstrap_pr and pr_number == bootstrap_pr:
        return True
    receipt = load_json(repo / "governance/active_receipt.json")
    incident_ids = receipt.get("incident_repair_ids", [])
    if isinstance(incident_ids, list) and incident_id in incident_ids:
        return True
    closed_ids = receipt.get("closed_incident_repair_ids", [])
    return (
        receipt.get("repair_kind") == "INCIDENT_REPAIR_CLOSURE"
        and isinstance(closed_ids, list)
        and incident_id in closed_ids
    )


def _blocking_scope(incident: dict) -> str:
    raw = incident.get("blocking_scope")
    require(
        isinstance(raw, str) and raw.strip(),
        f"open incident {incident['id']} lacks blocking_scope",
    )
    return raw.strip().upper()


def check_blocking_incidents(repo: Path, verify_git: bool) -> None:
    incidents = check_incident_history(repo, verify_git)
    event_mode = str(os.environ.get("N0TE2_EVENT_MODE") or "").strip().upper()
    current_rows = {row["id"]: row for row in _current_incident_rows(incidents)}

    if verify_git and event_mode == "PR":
        base = candidate_base(repo)
        if base is not None:
            base_rows = _current_incident_rows(git_jsonl(repo, base, "governance/incidents.jsonl"))
            for base_incident in base_rows:
                base_status = _validate_incident_status(base_incident)
                if not base_status.startswith("OPEN"):
                    continue
                base_scope = _blocking_scope(base_incident)
                if base_scope.startswith("NON_BLOCKING"):
                    continue
                candidate_incident = current_rows.get(base_incident["id"])
                require(candidate_incident is not None, f"blocking incident {base_incident['id']} disappeared from current incident truth")
                candidate_status = _validate_incident_status(candidate_incident)
                if not candidate_status.startswith("OPEN"):
                    require(
                        _explicit_incident_repair(repo, base_incident),
                        f"blocking incident {base_incident['id']} cannot transition to resolved outside its explicit incident-repair contract",
                    )

    for incident in current_rows.values():
        status = _validate_incident_status(incident)
        if not status.startswith("OPEN"):
            continue

        normalized_scope = _blocking_scope(incident)
        if normalized_scope.startswith("NON_BLOCKING"):
            continue

        require(
            normalized_scope == BLOCKING_REPAIR_SCOPE,
            f"open incident {incident['id']} blocks merge unless its durable record declares "
            f"NON_BLOCKING or {BLOCKING_REPAIR_SCOPE}",
        )
        contract = incident.get("repair_contract", {})
        require(
            contract.get("unrelated_merges_blocked") is True,
            f"blocking incident {incident['id']} repair contract must keep unrelated merges blocked",
        )
        require(
            contract.get("future_receipt_field") == "incident_repair_ids",
            f"blocking incident {incident['id']} must bind future repairs to incident_repair_ids",
        )
        if verify_git and event_mode == "PR":
            require(
                _explicit_incident_repair(repo, incident),
                f"blocking incident {incident['id']} permits only its explicit bootstrap "
                "or an ACTIVE receipt naming the incident",
            )


def check_handoff_lifecycle_contract(repo: Path) -> None:
    handoff = load_json(repo / "governance/handoff.json")
    current = load_json(repo / "governance/current_state.json")

    require(
        set(handoff.keys()) == HANDOFF_TOP_LEVEL_KEYS,
        "handoff authority surface changed or gained unreviewed shadow fields",
    )
    lifecycle = handoff.get("lifecycle")
    require(isinstance(lifecycle, dict), "handoff lifecycle compatibility hook must be an object when present")
    require(
        set(lifecycle.keys()).issubset(HANDOFF_COMPATIBILITY_KEYS),
        "handoff lifecycle compatibility hook contains unreviewed shadow fields",
    )

    key_map = {
        "state": "lifecycle_state",
        "active_node": "active_node",
        "active_increment": "active_increment",
    }
    for legacy_key, current_key in key_map.items():
        if legacy_key in lifecycle:
            require(
                exact_json_equal(lifecycle.get(legacy_key), current.get(current_key)),
                f"handoff compatibility {legacy_key} contradicts current_state",
            )

    derived = handoff.get("derived_runtime_truth")
    require(isinstance(derived, dict), "handoff derived_runtime_truth must be an object")
    require(
        set(derived.keys()) == set(HANDOFF_RUNTIME_TRUTH_CONTRACT.keys()),
        "handoff derived runtime authority surface changed or gained shadow fields",
    )
    for key, expected in HANDOFF_RUNTIME_TRUTH_CONTRACT.items():
        require(
            exact_json_equal(derived.get(key), expected),
            f"handoff lifecycle ownership declaration changed: {key}",
        )


def check_terminal_graph_resequencing(repo: Path, verify_git: bool) -> None:
    if not verify_git:
        return
    current = load_json(repo / "governance/current_state.json")
    if current.get("lifecycle_state") not in TERMINAL_LIFECYCLE_STATES:
        return

    changed = candidate_changed_paths(repo)
    if "governance/completion_graph.json" not in changed:
        return

    base = candidate_base(repo)
    require(base is not None, "terminal completion-graph change has no verifiable base")
    base_current = git_json(repo, base, "governance/current_state.json")
    require(
        base_current.get("lifecycle_state") == "ACTIVE",
        "terminal candidate cannot resequence completion graph from a non-ACTIVE base; "
        "reactivate construction with a bounded receipt first",
    )

    base_graph = git_json(repo, base, "governance/completion_graph.json")
    candidate_graph = load_json(repo / "governance/completion_graph.json")
    require(
        exact_json_equal(base_graph.get("schema_version"), candidate_graph.get("schema_version")),
        "terminal closure cannot change completion-graph schema",
    )

    base_nodes = {
        row.get("id"): row
        for row in base_graph.get("nodes", [])
        if isinstance(row, dict)
    }
    candidate_nodes = {
        row.get("id"): row
        for row in candidate_graph.get("nodes", [])
        if isinstance(row, dict)
    }
    require(base_nodes.keys() == candidate_nodes.keys(), "terminal closure cannot add or remove completion nodes")

    active_base = [node_id for node_id, row in base_nodes.items() if row.get("state") == "ACTIVE"]
    require(len(active_base) == 1, "terminal graph closure requires exactly one ACTIVE base node")

    for node_id, base_row in base_nodes.items():
        candidate_row = candidate_nodes[node_id]
        base_structure = {key: value for key, value in base_row.items() if key != "state"}
        candidate_structure = {key: value for key, value in candidate_row.items() if key != "state"}
        require(
            exact_json_equal(base_structure, candidate_structure),
            f"terminal closure cannot change completion-graph dependencies or semantics: {node_id}",
        )
        if node_id == active_base[0]:
            require(
                candidate_row.get("state") in {"PRESERVED", "DONE"},
                "terminal closure must close the previously ACTIVE node",
            )
        else:
            require(
                exact_json_equal(candidate_row.get("state"), base_row.get("state")),
                f"terminal closure changed unrelated node state: {node_id}",
            )


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
        f"{lifecycle} candidate changed construction-sensitive paths without ACTIVE bounded receipt: "
        + ", ".join(construction),
    )


def check_merge_policy(repo: Path) -> None:
    policy = load_json(repo / "governance/merge_policy.json")
    require(
        set(policy.keys()) == MERGE_POLICY_TOP_LEVEL_KEYS,
        "merge policy authority surface changed or gained unreviewed shadow fields",
    )
    require(policy.get("schema_version") == 5, "merge policy schema changed without Main Steward review")
    require(policy.get("target_branch") == "main", "merge policy target branch changed")
    require(
        exact_json_equal(policy.get("required_exact_head_status_contexts"), REQUIRED_PLATFORM_CONTEXTS),
        "required exact-head platform contexts changed",
    )

    requirements = policy.get("requirements", {})
    require(
        isinstance(requirements, dict) and set(requirements.keys()) == MERGE_REQUIREMENT_KEYS,
        "merge requirement authority surface changed or gained shadow fields",
    )
    for key in MERGE_REQUIREMENT_KEYS:
        require(requirements.get(key) is True, f"merge policy missing required Steward gate: {key}")

    steward_gate = policy.get("steward_gate", {})
    expected = {
        "required": True,
        "structural_status_context": "n0te2-steward-structure",
        "status_is_merge_authorization": False,
        "live_authorization_owner": "MAIN_STEWARD",
        "trusted_checker_source": "PR_BASE",
        "trusted_workflow_event": "pull_request_target",
        "bootstrap_requires_manual_steward_review": True,
        "bootstrap_pr": 211,
        "bootstrap_publishes_trusted_status": False,
        "pending_review_blocks_merge": True,
        "review_must_bind_exact_head": True,
        "post_merge_review_opens_fix_order": True,
        "open_incident_default": "BLOCK_UNLESS_EXPLICIT_NON_BLOCKING_OR_REPAIR",
        "receipt_prefix_contract": "NORMALIZED_DIRECTORY_BOUNDARY",
        "terminal_graph_resequencing": "ACTIVE_TRANSITION_REQUIRED",
        "trusted_gate_artifact_mutation": "MANUAL_META_GOVERNANCE_REOPEN_REQUIRED",
        "candidate_ci_token_write_policy": "FORBID_STATUS_AND_CHECK_WRITE",
        "pr_diff_mode": "MERGE_BASE_TO_HEAD",
        "main_push_diff_mode": "EXACT_BASE_TO_HEAD_TREE",
        "incident_history_contract": "APPEND_PRESERVE_BASE",
        "trusted_status_reset_before_evaluation": True,
        "trusted_checkout_path_mode": "RUNTIME_UNIQUE",
        "meta_governance_reopen_label": META_GOVERNANCE_REOPEN_LABEL,
        "meta_governance_reopen_env_value": META_GOVERNANCE_REOPEN_VALUE,
    }
    require(
        isinstance(steward_gate, dict) and set(steward_gate.keys()) == set(expected.keys()) | {"rule"},
        "Steward gate authority surface changed or gained shadow fields",
    )
    for key, value in expected.items():
        require(
            exact_json_equal(steward_gate.get(key), value),
            f"Steward gate policy drifted: {key}",
        )
    rule = steward_gate.get("rule")
    require(
        isinstance(rule, str)
        and "It never authorizes merge by itself" in rule
        and "single Main Steward" in rule
        and META_GOVERNANCE_REOPEN_LABEL in rule,
        "Steward gate explanatory rule lost non-authorizing or meta-governance truth",
    )
    require(
        exact_json_equal(policy.get("external_enforcement"), EXTERNAL_ENFORCEMENT_EXPECTED),
        "external enforcement truth changed or gained shadow fields",
    )


def check_steward_actor(repo: Path) -> None:
    registry = load_json(repo / "governance/automation_registry.json")
    require(
        set(registry.keys()) == REGISTRY_TOP_LEVEL_KEYS,
        "automation registry authority surface changed or gained unreviewed shadow fields",
    )
    require(registry.get("supervisor") == "N0TE-SUPERVISOR", "automation registry supervision root changed")
    actors = registry.get("actors", [])
    actor = next(
        (
            row
            for row in actors
            if isinstance(row, dict) and row.get("id") == "AUTO-STEWARD-INTEGRATION-GATE-001"
        ),
        None,
    )
    require(actor is not None, "Steward integration workflow is missing from the supervision graph")
    require(
        set(actor.keys()) == STEWARD_ACTOR_TOP_LEVEL_KEYS,
        "Steward actor authority surface changed or gained shadow fields",
    )

    expected_fields = {
        "kind": "GITHUB_ACTIONS",
        "role_class": "CI_STRUCTURAL_VERIFIER",
        "path": ".github/workflows/steward-integration.yml",
        "parent": "N0TE-SUPERVISOR",
        "reports_to": "N0TE-SUPERVISOR",
        "purpose": "Verify exact-head Steward integration structure with a base-owned checker after bootstrap, without claiming live merge authorization.",
        "boundary": "Structural evidence only. The workflow cannot replace the Main Steward's live review, incident, race, expected-head merge, or post-merge checks. The bootstrap PR publishes no trusted Steward status because its base does not yet contain this workflow/checker.",
        "authority": "VERIFY_STRUCTURE_ONLY",
        "allowed_mutations": ["CI_STATUS_CONTEXT"],
        "escalation_target": "N0TE-SUPERVISOR",
        "auto_spawn_successor": False,
    }
    for key, expected in expected_fields.items():
        require(
            exact_json_equal(actor.get(key), expected),
            f"Steward actor authority contract drifted: {key}",
        )

    observability = actor.get("observability", {})
    expected_observability = {
        "exact_head_required": True,
        "reactivation_is_event": True,
        "observation_artifact": "external://github/commit-status/n0te2-steward-structure",
        "trusted_status_context": "n0te2-steward-structure",
        "trusted_checker_source": "PR_BASE",
        "trusted_workflow_event": "pull_request_target",
        "merge_authorization_owner": "MAIN_STEWARD",
    }
    require(
        isinstance(observability, dict) and set(observability.keys()) == set(expected_observability.keys()),
        "Steward actor observability authority surface changed or gained shadow fields",
    )
    for key, expected in expected_observability.items():
        require(
            exact_json_equal(observability.get(key), expected),
            f"Steward actor observability/authority contract drifted: {key}",
        )


def check_workflow_contracts(repo: Path) -> None:
    ordinary = (repo / ".github/workflows/governance.yml").read_text()
    require("\n  pull_request:\n" in ordinary, "ordinary exact-head governance must remain a pull_request workflow")
    require(
        "statuses: write" not in ordinary and "checks: write" not in ordinary,
        "candidate-executing governance workflow must not hold status/check write authority",
    )
    require("persist-credentials: false" in ordinary, "candidate checkout must not persist GitHub credentials")
    require(
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in ordinary,
        "ordinary exact-head checkout action must remain pinned",
    )
    require(
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in ordinary,
        "ordinary exact-head Python setup action must remain pinned",
    )
    require(
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in ordinary,
        "ordinary exact-head artifact upload action must remain pinned",
    )
    for context in REQUIRED_PLATFORM_CONTEXTS:
        require(context in ordinary, f"ordinary workflow lost canonical platform job name: {context}")

    steward = (repo / ".github/workflows/steward-integration.yml").read_text()
    require("pull_request_target:" in steward, "trusted Steward workflow must be base-owned pull_request_target")
    require("labeled" in steward and "unlabeled" in steward, "trusted Steward workflow must re-run on meta-governance label changes")
    require("edited" in steward, "trusted Steward workflow must re-run when PR base metadata is edited")
    require("\n  pull_request:\n" not in steward, "trusted Steward workflow must not execute from candidate pull_request definition")
    require("persist-credentials: false" in steward, "trusted Steward checkout must not persist credentials into candidate worktree")
    require("--depth=1" not in steward, "trusted base fetch must not truncate merge-base history")
    require("N0TE2_DIFF_MODE: PR_MERGE_BASE" in steward, "trusted PR workflow must request merge-base diff semantics")
    require("N0TE2_DIFF_MODE: EXACT_TREE" in steward, "main push workflow must request exact base-to-head tree diff semantics")
    require(
        "STATUS_STATE: pending" in steward and "Reset trusted structural status to pending" in steward,
        "trusted structural context must reset to pending before evaluation",
    )
    require(META_GOVERNANCE_REOPEN_LABEL in steward, "trusted workflow lost the manual meta-governance reopen label")
    require(
        "TRUSTED_PATH: .steward-trusted-${{ github.run_id }}-${{ github.run_attempt }}" in steward,
        "trusted base checkout path must be runtime-unique",
    )
    require(
        "$TRUSTED_PATH/governance/check_steward_integration.py" in steward,
        "trusted workflow must execute the PR-base checker from the runtime-unique trusted path",
    )
    require(
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in steward,
        "trusted Steward checkout action must remain pinned",
    )
    require(
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in steward,
        "trusted Steward Python setup action must remain pinned",
    )


def _meta_governance_reopen_authorized() -> bool:
    return str(os.environ.get(META_GOVERNANCE_REOPEN_ENV) or "").strip() == META_GOVERNANCE_REOPEN_VALUE


def check_trusted_artifact_immutability(repo: Path, verify_git: bool) -> None:
    if not verify_git:
        return
    if str(os.environ.get("N0TE2_EVENT_MODE") or "").strip().upper() != "PR":
        return

    base = candidate_base(repo)
    require(base is not None, "trusted PR artifact comparison has no base")
    pr_number = str(os.environ.get("N0TE2_PR_NUMBER") or "").strip()
    bootstrap = pr_number == BOOTSTRAP_PR_NUMBER
    meta_reopen = _meta_governance_reopen_authorized()

    workflow_changes = [
        path
        for path in candidate_changed_paths(repo)
        if path.casefold().startswith(PRIVILEGED_WORKFLOW_PREFIX.casefold())
    ]
    require(
        not workflow_changes or bootstrap or meta_reopen,
        "ordinary PR changed privileged workflow surface without explicit Main Steward meta-governance reopen: "
        + ", ".join(workflow_changes),
    )

    for relative in TRUSTED_GATE_ARTIFACTS:
        try:
            base_bytes = git_show_bytes(repo, base, relative)
        except StewardIntegrationError:
            if bootstrap:
                continue
            raise

        candidate_bytes = git_show_bytes(repo, "HEAD", relative)
        if candidate_bytes == base_bytes:
            continue

        require(
            bootstrap or meta_reopen,
            f"trusted gate artifact changed in an ordinary PR: {relative}; "
            f"apply trusted label {META_GOVERNANCE_REOPEN_LABEL} for an explicit Main Steward meta-governance reopen",
        )


def run(repo: Path, verify_git: bool = True) -> None:
    check_candidate_inputs_are_regular(repo)
    check_canonical_extensions(repo)
    check_merge_policy(repo)
    check_steward_actor(repo)
    check_workflow_contracts(repo)
    check_receipt_path_boundaries(repo)
    check_lifecycle_and_active_receipt(repo, verify_git)
    check_blocking_incidents(repo, verify_git)
    check_handoff_lifecycle_contract(repo)
    check_trusted_artifact_immutability(repo, verify_git)
    check_terminal_graph_resequencing(repo, verify_git)
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