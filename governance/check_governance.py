#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path

HEX40 = re.compile(r"^[0-9a-f]{40}$")
REQ_ID = re.compile(r"^REQ-SCOPE-\d{3}$")
LIFECYCLE_STATES = {"ACTIVE", "STABLE", "WAITING", "BLOCKED"}
TERMINAL_LIFECYCLE_STATES = {"STABLE", "WAITING", "BLOCKED"}
INCIDENT_REPAIR_NODE = "INCIDENT-REPAIR"
INCIDENT_REPAIR_KIND = "INCIDENT_REPAIR"
INCIDENT_REPAIR_TARGET_KINDS = {"GOVERNANCE", "MERGED_PRODUCT"}


class GovernanceError(RuntimeError):
    pass


def expand_requirement_expr(expr):
    if isinstance(expr, list):
        return expr
    if not expr:
        return []
    out = []
    for token in str(expr).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            for n in range(int(a), int(b) + 1):
                out.append(f"REQ-SCOPE-{n:03d}")
        else:
            out.append(f"REQ-SCOPE-{int(token):03d}")
    return out


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        raise GovernanceError(f"cannot load {path}: {exc}") from exc


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
        raise GovernanceError(f"cannot load {path}: {exc}") from exc
    return rows


def require(cond: bool, msg: str):
    if not cond:
        raise GovernanceError(msg)


def check_requirements(repo: Path):
    doc = load_json(repo / "governance/requirements.json")
    start, end = doc["sequence"]["start"], doc["sequence"]["end"]
    require((start, end) == (2, 153), "requirement sequence must be exactly REQ-SCOPE-002..153")
    ids = [f"REQ-SCOPE-{n:03d}" for n in range(start, end + 1)]
    held = set(doc.get("held_or_boundary", []))
    superseded = set(doc.get("superseded", []))
    require(not (held & superseded), "requirement cannot be both held and superseded")
    require((held | superseded).issubset(ids), "non-active requirement outside canonical sequence")
    require(doc.get("default_classification") == "KNOWN", "default requirement classification must be KNOWN, not selected work")
    require(doc.get("known_blocks_candidate") is True, "known unresolved requirements must block candidate completion")
    require(doc.get("known_selects_work") is False, "known scope must not select work by itself")
    require(doc.get("non_active_blocks_candidate") is False, "non-active requirements cannot block candidate")
    rows = {}
    for rid in ids:
        require(REQ_ID.match(rid) is not None, f"bad requirement id: {rid}")
        classification = "HELD_OR_BOUNDARY" if rid in held else "SUPERSEDED" if rid in superseded else "KNOWN"
        rows[rid] = {"id": rid, "classification": classification, "blocks_candidate": classification == "KNOWN", "selects_work": False}
    return rows


def check_graph(repo: Path, requirements):
    nodes = load_json(repo / "governance/completion_graph.json")["nodes"]
    ids = [n["id"] for n in nodes]
    require(len(ids) == 48, f"expected 48 completion nodes, found {len(ids)}")
    require(len(ids) == len(set(ids)), "duplicate graph node IDs")
    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        require(n["autonomy"] in {"GREEN", "AMBER", "RED"}, f"bad autonomy: {n['id']}")
        require(n["state"] in {"DONE", "ACTIVE", "PRESERVED", "LATER"}, f"bad state: {n['id']}")
        require(n["dependency_mode"] in {"ROOT", "ALL", "ALL+ANY"}, f"bad dependency mode: {n['id']}")
        deps = n.get("depends_on", [])
        any_of = n.get("any_of", [])
        if n["dependency_mode"] == "ROOT":
            require(not deps and not any_of, f"root node has dependencies: {n['id']}")
        else:
            require(bool(deps), f"non-root node lacks dependencies: {n['id']}")
        if n["dependency_mode"] == "ALL+ANY":
            require(bool(any_of), f"ALL+ANY node lacks any_of: {n['id']}")
        else:
            require(not any_of, f"unexpected any_of on {n['id']}")
        for dep in deps + any_of:
            require(dep in by_id, f"unknown dependency {dep} from {n['id']}")
        for rid in expand_requirement_expr(n.get("requirements", "")):
            require(rid in requirements, f"unknown requirement {rid} from {n['id']}")

    indegree = {nid: 0 for nid in ids}
    children = defaultdict(list)
    for n in nodes:
        for dep in n.get("depends_on", []) + n.get("any_of", []):
            children[dep].append(n["id"])
            indegree[n["id"]] += 1
    q = deque([nid for nid, degree in indegree.items() if degree == 0])
    seen = []
    while q:
        current = q.popleft()
        seen.append(current)
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                q.append(child)
    require(len(seen) == len(ids), "completion graph contains a dependency cycle")

    mapped = set()
    for n in nodes:
        if n["required"]:
            mapped.update(expand_requirement_expr(n.get("requirements", "")))
    orphan = [rid for rid, row in requirements.items() if row["classification"] == "KNOWN" and rid not in mapped]
    require(not orphan, f"orphan active requirements (known scope mapping): {', '.join(orphan)}")
    later = by_id["LATER-01"]
    held = {rid for rid, row in requirements.items() if row["classification"] == "HELD_OR_BOUNDARY"}
    require(held.issubset(set(expand_requirement_expr(later.get("requirements", "")))), "LATER-01 does not preserve every held/boundary requirement")
    superseded = {rid for rid, row in requirements.items() if row["classification"] == "SUPERSEDED"}
    require(not (superseded & mapped), "superseded requirement leaked into required candidate coverage")

    six = {f"DAW-0{i}" for i in range(1, 7)}
    daw_gate = by_id["DAW-TEST-READY"]
    require(daw_gate["dependency_mode"] == "ALL", "DAW-TEST-READY must be ALL")
    require(set(daw_gate["depends_on"]) == ({"DAW-00", "DAW-07"} | six), "DAW-TEST-READY must require DAW-00, all six core DAWs, and DAW-07")
    platform_gate = by_id["PLATFORM-TEST-READY"]
    require(platform_gate["dependency_mode"] == "ALL", "platform gate must remain ALL")
    require(set(platform_gate["depends_on"]) == {"PLATFORM-00", "PLATFORM-01", "PLATFORM-02", "PLATFORM-03"}, "platform gate must require macOS+Windows+Linux")
    cand = by_id["CAND-01"]
    require(set(cand["depends_on"]) == {"CONV-01", "DAW-TEST-READY", "PLATFORM-TEST-READY"}, "CAND-01 gate narrowed")
    require(by_id["TEST-01"]["depends_on"] == ["CAND-01"], "TEST-01 must never precede CAND-01")
    require({"REQ-SCOPE-148", "REQ-SCOPE-150"}.issubset(set(expand_requirement_expr(by_id["DAW-07"]["requirements"]))), "DAW-07 lost Generic Other/plugin baseline")
    require("AUDIO-02" in by_id["DAW-07"]["depends_on"], "DAW-07 flattened to manual-only: AUDIO-02 missing")
    active_nodes = [n["id"] for n in nodes if n["state"] == "ACTIVE"]
    require(len(active_nodes) <= 1, f"at most one graph node may be ACTIVE, found {active_nodes}")
    return by_id


def check_platforms(repo: Path):
    p = load_json(repo / "governance/platform_support.json")
    policy = p["policy"]
    require(policy["core_platforms"] == ["macOS", "Windows", "Linux"], "core platform parity must be macOS+Windows+Linux")
    require(policy["version_name_alone_is_breakpoint"] is False, "OS marketing name cannot be a support breakpoint")
    require(policy["unsupported_requires_named_break"] is True, "unsupported must require a named break")
    expected = {"macOS": {"arm64", "x86_64"}, "Windows": {"x86_64", "arm64"}, "Linux": {"x86_64", "aarch64"}}
    actual = {key: set(value) for key, value in p["core_architectures"].items()}
    require(actual == expected, f"core architecture matrix narrowed: {actual}")
    linux = p["linux_packaging"]
    require({"AppImage", "tar.zst"}.issubset(set(linux["broad_baseline"])), "Linux portable baseline missing")
    require({"deb", "rpm"}.issubset(set(linux["native"])), "Linux native package families missing")
    return p


def check_plugins(repo: Path):
    p = load_json(repo / "governance/plugin_contract.json")
    require(p["universal_n0te_capability"] is True, "plugin capability cannot belong to one DAW")
    require(p["scan_standard_locations"] is True, "standard plugin paths must be scanned")
    require(p["ask_for_additional_locations"] is True, "must ask for additional plugin locations")
    require(p["persist_custom_locations"] is True, "custom plugin paths must persist")
    require(p["silent_arbitrary_disk_crawl"] is False, "silent whole-disk crawl forbidden")
    required_formats = {"VST3", "AU", "CLAP", "LV2", "LADSPA", "AAX"}
    require(required_formats.issubset(set(p["formats"])), "plugin format model flattened")
    require(p["formats"]["AAX"]["standalone_direct_host_claim"] is False, "AAX standalone hosting must not be claimed without proof")
    require(p["prefer_out_of_process_architecture_specific_workers"] is True, "plugin workers must preserve architecture isolation preference")


def check_authority(repo: Path):
    a = load_json(repo / "governance/authority.json")
    joined = "\n".join(a["current_authority_files"])
    for marker in a["forbidden_current_authority_markers"]:
        require(marker not in joined, f"stale authority reintroduced: {marker}")
    required = {"AGENTS.md","governance/legacy_admission.json","governance/handoff.json","governance/invariants.json","governance/decisions.jsonl","governance/incidents.jsonl","governance/automation_registry.json","governance/controller_versions.jsonl","governance/provenance.jsonl","governance/definitions.jsonl","governance/trajectory_audits.jsonl","governance/merge_policy.json","governance/build_handoff.py"}
    require(required.issubset(set(a["current_authority_files"])), "retention/supervision authority surface is incomplete")
    laws = a["laws"]
    require(laws["implementation_maturity_must_not_mutate_scope"] is True, "anti-flattening law missing")
    require(laws["missing_acceptance_resource_stops_unrelated_construction"] is False, "resource-wait loop reintroduced")
    require(laws["legacy_classification_is_not_copy_authority"] is True, "classification became copy authority")
    require(laws["legacy_test_green_is_not_product_completion"] is True, "legacy tests became product-completion authority")
    require(laws["construction_can_terminate_at_stability"] is True, "construction must be able to terminate at stability")
    require(laws["known_scope_does_not_select_work"] is True, "known scope cannot silently select work")
    require(laws["automation_must_report_to_supervision_graph"] is True, "automation escaped supervision graph")
    require(laws["reactivation_must_be_observable"] is True, "reactivation must be observable")
    require(laws["durable_handoff_precedes_historical_archaeology"] is True, "durable handoff must precede archaeology")
    require(laws["exact_head_observations_required"] is True, "exact-head observation law missing")
    require(laws["decisions_and_incidents_retain_provenance"] is True, "decision/incident provenance law missing")


def check_legacy_admission(repo: Path):
    p = load_json(repo / "governance/legacy_admission.json")
    require(p["node_id"] == "LEGACY-01", "legacy admission manifest must bind LEGACY-01")
    source = p["source"]
    require(source["repository"] == "syrustkira/N0TE", "legacy source repository changed")
    require(source["commit_sha"] == "25e2f2f5fd8ea4adc0c7e61650531430025e59db", "legacy source commit changed")
    require(source["tree_sha"] == "fb7eb7a9d3a6f2e1620dfbbfca7f21b51975b7c4", "legacy source tree changed")
    census = p["census"]
    require(census["total_leaf_files"] == 331, "legacy total asset count changed")
    require(census["classified_leaf_files"] == 331, "legacy classified asset count changed")
    require(census["unclassified_leaf_files"] == 0, "unclassified legacy assets remain")
    require(sum(census["families"].values()) == census["total_leaf_files"], "legacy family counts do not reconcile to total")
    require(set(p["allowed_dispositions"]) == {"REUSE_AS_IS", "ADAPT", "REPLACE", "ARCHIVE", "REJECT"}, "legacy disposition vocabulary changed")
    rights = p["rights"]
    require(rights["license_gate_id"] == "LICENSE-GATE-001", "LICENSE-GATE-001 missing")
    require(rights["direct_legacy_source_copy_authorized"] is False, "direct legacy source copy cannot self-authorize")
    require(rights["direct_legacy_test_text_copy_authorized"] is False, "direct legacy test text copy cannot self-authorize")
    require(rights["fresh_reimplementation_default"] is True, "fresh reimplementation must remain the conservative default")
    require(rights["rights_resolution_required_for_direct_copy"] is True, "direct copy must remain rights-gated")
    evidence = p["selected_spec_evidence"]
    require(evidence["behavior_groups"] == 11, "selected behavior-group count changed")
    require(evidence["selected_legacy_test_files"] == 19, "selected legacy-test-file count changed")
    require(evidence["legacy_green_does_not_close_product_nodes"] is True, "legacy green cannot close product nodes")
    required_gaps = {"CONNECTED_TO_OFFLINE_PENDING_STATE_CHOICE","OFFLINE_TO_CONNECTED_EXPLICIT_RECONCILIATION_CHOICE","PRIVATE_DATA_EGRESS_EXACT_DATA_DESTINATION_PURPOSE","CORRUPTION_VISIBILITY_AND_RECOVERY"}
    require(required_gaps.issubset(set(evidence["n0te2_replacement_regression_gaps"])), "N0TE2 replacement regression gaps were dropped")
    required_contamination = {"ABLETON_SEMANTIC_PRIORITY","MANUAL_FALLBACK_AS_PRODUCT_IDENTITY","DEEPEST_ADAPTER_OR_FIXED_TIER_RANKING","DUPLICATE_PRODUCT_OR_COMPLETION_AUTHORITY","PARALLEL_STATE_STORE_SPIDERWEB","MONOLITHIC_SERVER_OR_ROUTE_MONKEY_PATCHING","PLAINTEXT_PERSISTENT_SECRET_FALLBACK","SILENT_CORRUPTION_TO_EMPTY_STATE","SILENT_FIXED_HISTORY_TRUNCATION","HISTORICAL_PLATFORM_OR_VERSION_FLOORS_AS_SCOPE","GENERIC_NAMES_HIDING_HOST_SPECIFIC_IMPLEMENTATION"}
    require(required_contamination.issubset(set(p["contamination_classes"])), "required contamination classes were dropped")
    require(all(p["admission_law"].values()), "legacy admission law weakened")
    return p


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT).strip()


def git_path_exists(repo: Path, commit_sha: str, path: str) -> bool:
    try:
        subprocess.check_output(
            ["git", "cat-file", "-e", f"{commit_sha}:{path}"],
            cwd=repo,
            stderr=subprocess.STDOUT,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def path_allowed(path: str, receipt: dict) -> bool:
    return path in set(receipt.get("allowed_exact_paths", [])) or any(path.startswith(prefix) for prefix in receipt.get("allowed_prefixes", []))


def _current_incident(rows: list[dict], incident_id: str) -> dict | None:
    found = None
    for row in rows:
        if row.get("id") == incident_id:
            found = row
    return found


def _check_incident_repair_receipt(repo: Path, receipt: dict, verify_git: bool, current: dict) -> None:
    require(receipt.get("repair_kind") == INCIDENT_REPAIR_KIND, "INCIDENT-REPAIR requires repair_kind=INCIDENT_REPAIR")
    target_kind = receipt.get("repair_target_kind")
    require(target_kind in INCIDENT_REPAIR_TARGET_KINDS, f"unsupported incident repair target kind: {target_kind}")
    repair_issue = receipt.get("repair_issue")
    require(type(repair_issue) is int and repair_issue > 0, "incident repair requires a positive integer repair_issue")
    incident_ids = receipt.get("incident_repair_ids")
    require(isinstance(incident_ids, list) and incident_ids, "incident repair requires incident_repair_ids")
    require(all(isinstance(item, str) and item.strip() for item in incident_ids), "incident repair ids must be non-empty text")
    require(len(incident_ids) == len(set(incident_ids)), "incident repair ids must be unique")
    require(receipt.get("product_code_allowed") == current.get("product_code_authorized"), "incident repair receipt/current-state product authority mismatch")

    incidents = load_jsonl(repo / "governance/incidents.jsonl")
    incident_rows = []
    for incident_id in incident_ids:
        row = _current_incident(incidents, incident_id)
        require(row is not None, f"incident repair names unknown incident: {incident_id}")
        status = str(row.get("status") or "").strip().upper()
        require(status.startswith("OPEN"), f"incident repair names non-open incident: {incident_id}")
        repair_contract = row.get("repair_contract")
        require(isinstance(repair_contract, dict), f"incident repair {incident_id} lacks repair_contract")
        require(repair_contract.get("future_receipt_field") == "incident_repair_ids", f"incident repair {incident_id} does not permit receipt-bound repair")
        incident_rows.append(row)

    if target_kind == "GOVERNANCE":
        require(receipt.get("product_code_allowed") is False, "GOVERNANCE incident repair cannot authorize product code")
    else:
        require(receipt.get("product_code_allowed") is True, "MERGED_PRODUCT incident repair must explicitly authorize bounded product code")
        target_sha = receipt.get("repair_target_merge_sha")
        require(isinstance(target_sha, str) and HEX40.fullmatch(target_sha), "MERGED_PRODUCT incident repair requires exact repair_target_merge_sha")
        if verify_git:
            for row in incident_rows:
                discovery_sha = row.get("evidence", {}).get("main_at_discovery")
                require(isinstance(discovery_sha, str) and HEX40.fullmatch(discovery_sha), f"incident {row.get('id')} lacks exact main_at_discovery evidence")
                try:
                    git(repo, "merge-base", "--is-ancestor", target_sha, discovery_sha)
                except subprocess.CalledProcessError as exc:
                    raise GovernanceError(
                        f"repair target {target_sha} does not predate incident discovery main {discovery_sha}"
                    ) from exc

    if not verify_git:
        return

    baseline = receipt["baseline_sha"]
    changed = [path for path in git(repo, "diff", "--no-renames", "--name-only", f"{baseline}...HEAD").splitlines() if path]
    if target_kind == "GOVERNANCE":
        bad = [
            path
            for path in changed
            if not (path.startswith("governance/") or path.startswith("tests/governance/"))
        ]
        require(not bad, "GOVERNANCE incident repair changed non-governance paths: " + ", ".join(bad))
    else:
        target_sha = receipt["repair_target_merge_sha"]
        product_paths = [
            path
            for path in changed
            if path.startswith("n0te2/")
            or (path.startswith("tests/") and not path.startswith("tests/governance/"))
            or path in {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"}
        ]
        new_paths = [path for path in product_paths if not git_path_exists(repo, target_sha, path)]
        require(
            not new_paths,
            "MERGED_PRODUCT incident repair introduced construction paths absent from the target merge: "
            + ", ".join(new_paths),
        )


def check_receipt(repo: Path, verify_git: bool, current: dict):
    receipt = load_json(repo / "governance/active_receipt.json")
    active = current["active_node"]
    increment = str(current.get("active_increment") or "").strip()

    if active == "BOOT-02":
        expected_receipt = "N0TE2-BOOT-02"
    elif active == "LEGACY-01":
        expected_receipt = "N0TE2-LEGACY-01"
    else:
        require(bool(increment), f"{active} product construction requires an active_increment")
        expected_receipt = f"N0TE2-{increment}"
        require(receipt.get("increment_id") == increment, "receipt increment must match current active_increment")
        require(increment.startswith(active), f"active increment {increment} must belong to active node {active}")

    require(receipt["receipt_id"] == expected_receipt, "unexpected active receipt")
    require(receipt["node_id"] == active, "receipt must bind current active node")
    require(receipt.get("status") == "ACTIVE", "ACTIVE lifecycle requires an ACTIVE receipt")
    baseline = receipt["baseline_sha"]
    require(isinstance(baseline, str) and HEX40.fullmatch(baseline), "receipt baseline_sha must be exact 40-char lowercase SHA")

    if active == "BOOT-02":
        require(receipt["product_code_allowed"] is False, "BOOT-02 receipt cannot allow product code")
        require(receipt["legacy_admission_allowed"] is False, "BOOT-02 cannot authorize legacy admission")
    elif active == "LEGACY-01":
        require(receipt["product_code_allowed"] is False, "LEGACY-01 receipt cannot allow product code")
        require(receipt["legacy_admission_allowed"] is True, "LEGACY-01 must authorize migration evidence work")
        require(receipt["legacy_source_copy_allowed"] is False, "LEGACY-01 cannot authorize direct legacy source copy")
        require(receipt["legacy_test_text_copy_allowed"] is False, "LEGACY-01 cannot authorize direct legacy test-text copy")
    elif active == INCIDENT_REPAIR_NODE:
        require(receipt["legacy_admission_allowed"] is False, "incident repair cannot reopen legacy admission")
        require(receipt["legacy_source_copy_allowed"] is False, "incident repair cannot authorize direct legacy source copy")
        require(receipt["legacy_test_text_copy_allowed"] is False, "incident repair cannot authorize direct legacy test-text copy")
        require(bool(receipt.get("allowed_prefixes") or receipt.get("allowed_exact_paths")), "incident repair must define bounded allowed paths")
        _check_incident_repair_receipt(repo, receipt, verify_git, current)
    else:
        require(receipt["product_code_allowed"] is True, f"{active} product increment must explicitly authorize bounded product code")
        require(receipt["legacy_admission_allowed"] is False, f"{active} cannot reopen legacy admission")
        require(receipt["legacy_source_copy_allowed"] is False, f"{active} cannot authorize direct legacy source copy")
        require(receipt["legacy_test_text_copy_allowed"] is False, f"{active} cannot authorize direct legacy test-text copy")
        require(bool(receipt.get("allowed_prefixes")), "product receipt must define bounded allowed paths")

    if verify_git:
        require(git(repo, "rev-parse", "--is-inside-work-tree") == "true", "not a git worktree")
        expected_head = os.environ.get("N0TE2_HEAD_SHA") or os.environ.get("EVIDENCE_SHA")
        if expected_head:
            actual_head = git(repo, "rev-parse", "HEAD")
            require(actual_head == expected_head, f"exact-head mismatch: expected {expected_head}, got {actual_head}")
        try:
            git(repo, "merge-base", "--is-ancestor", baseline, "HEAD")
        except subprocess.CalledProcessError as exc:
            raise GovernanceError(f"receipt baseline {baseline} is not an ancestor of HEAD: {exc.output}") from exc
        changed = [path for path in git(repo, "diff", "--no-renames", "--name-only", f"{baseline}...HEAD").splitlines() if path]
        bad = [path for path in changed if not path_allowed(path, receipt)]
        require(not bad, f"changed paths outside {active} receipt: {', '.join(bad)}")
        forbidden = tuple(receipt.get("forbidden_prefixes", []))
        bad_forbidden = [path for path in changed if forbidden and path.startswith(forbidden)]
        require(not bad_forbidden, f"forbidden clean-room paths changed during {active}: {', '.join(bad_forbidden)}")
    return receipt


def check_stage(repo: Path, graph: dict):
    current = load_json(repo / "governance/current_state.json")
    lifecycle = current.get("lifecycle_state")
    active = current.get("active_node")
    active_increment = current.get("active_increment")
    active_nodes = [node for node, row in graph.items() if row["state"] == "ACTIVE"]
    require(lifecycle in LIFECYCLE_STATES, f"invalid lifecycle_state: {lifecycle}")
    held = load_json(repo / "governance/held_scope.json")
    held_ids = {item["id"] for item in held["items"]}
    require(active not in held_ids, "held item became active")
    require(held_ids == {f"HOLD-{n:03d}" for n in range(1, 8)}, "held scope changed without explicit promotion")
    for done in current.get("completed_nodes", []):
        require(done in graph and graph[done]["state"] == "DONE", f"completed node not DONE in graph: {done}")
    if lifecycle in TERMINAL_LIFECYCLE_STATES:
        require(active is None, f"{lifecycle} cannot retain an active_node")
        require(active_increment is None, f"{lifecycle} cannot retain an active_increment")
        require(not active_nodes, f"{lifecycle} requires zero ACTIVE graph nodes")
        require(current["product_code_authorized"] is False, f"{lifecycle} cannot authorize construction")
        require(current["legacy_admission_authorized"] is False, f"{lifecycle} cannot authorize legacy admission")
        if lifecycle == "STABLE":
            require(bool(str(current.get("terminal_reason") or "").strip()), "STABLE requires terminal_reason")
        else:
            require(bool(str(current.get("wake_condition") or "").strip()), f"{lifecycle} requires wake_condition")
        return current

    if active == INCIDENT_REPAIR_NODE:
        require(not active_nodes, "INCIDENT-REPAIR cannot make a completion-graph node ACTIVE")
        require(isinstance(active_increment, str) and active_increment.startswith("INCIDENT-REPAIR-"), "INCIDENT-REPAIR requires a bounded incident-repair increment")
        require(type(current.get("product_code_authorized")) is bool, "INCIDENT-REPAIR product authority must be a JSON boolean")
        require(current["legacy_admission_authorized"] is False, "INCIDENT-REPAIR cannot keep legacy admission active")
        return current

    require(active in graph, "ACTIVE lifecycle requires current active node in completion graph")
    require(graph[active]["state"] == "ACTIVE", "current active node must be ACTIVE in graph")
    require(active_nodes == [active], "graph/current-state active node mismatch")
    if active == "BOOT-02":
        require(current["product_code_authorized"] is False, "BOOT-02 cannot authorize product code")
        require(current["legacy_admission_authorized"] is False, "BOOT-02 cannot authorize legacy admission")
    elif active == "LEGACY-01":
        require(graph["BOOT-02"]["state"] == "DONE", "LEGACY-01 cannot start before BOOT-02 is DONE")
        require("BOOT-02" in current.get("completed_nodes", []), "BOOT-02 missing from completed nodes")
        require(current["product_code_authorized"] is False, "LEGACY-01 cannot authorize product code")
        require(current["legacy_admission_authorized"] is True, "LEGACY-01 migration evidence must be authorized")
        check_legacy_admission(repo)
    else:
        deps = graph[active].get("depends_on", [])
        incomplete = [dep for dep in deps if graph[dep]["state"] != "DONE"]
        require(not incomplete, f"{active} cannot activate before dependencies are DONE: {', '.join(incomplete)}")
        require(current["product_code_authorized"] is True, f"{active} product construction must explicitly authorize product code")
        require(current["legacy_admission_authorized"] is False, f"{active} cannot keep legacy admission active")
        increment = str(active_increment or "").strip()
        require(bool(increment), f"{active} requires a bounded active increment")
        require(increment.startswith(active), f"active increment {increment} must belong to active node {active}")
        check_legacy_admission(repo)
    return current


def check_jsonl_ids(repo: Path, rel: str):
    rows = load_jsonl(repo / rel)
    require(rows, f"{rel} must contain at least one durable record")
    ids = [row.get("id") for row in rows]
    require(all(isinstance(item, str) and item.strip() for item in ids), f"{rel} record missing stable id")
    require(len(ids) == len(set(ids)), f"{rel} contains duplicate stable ids")
    return rows


def check_retention_surfaces(repo: Path, current: dict):
    invariants = load_json(repo / "governance/invariants.json")
    required_invariants = {"INV-SUP-001","INV-SUP-002","INV-LIFE-001","INV-LIFE-002","INV-SCOPE-001","INV-EVID-001","INV-HANDOFF-001","INV-AUTH-001"}
    rows = invariants.get("constitutional", [])
    ids = [row.get("id") for row in rows]
    require(len(ids) == len(set(ids)), "invariant registry contains duplicate IDs")
    require(required_invariants.issubset(set(ids)), "constitutional retention/supervision invariant missing")
    decisions = check_jsonl_ids(repo, "governance/decisions.jsonl")
    incidents = check_jsonl_ids(repo, "governance/incidents.jsonl")
    controllers = check_jsonl_ids(repo, "governance/controller_versions.jsonl")
    provenance = check_jsonl_ids(repo, "governance/provenance.jsonl")
    definitions = check_jsonl_ids(repo, "governance/definitions.jsonl")
    trajectories = check_jsonl_ids(repo, "governance/trajectory_audits.jsonl")
    for collection, name in ((decisions,"decision"),(incidents,"incident"),(controllers,"controller"),(provenance,"provenance"),(definitions,"definition"),(trajectories,"trajectory audit")):
        require(all(row.get("recorded_at") for row in collection), f"{name} record missing recorded_at")
    require(all(row.get("version") for row in definitions), "definition record missing version")
    require(all(row.get("kind") in {"CONSTITUTIONAL", "DERIVED"} for row in definitions), "definition kind must be CONSTITUTIONAL or DERIVED")

    automation = load_json(repo / "governance/automation_registry.json")
    require(automation.get("supervisor") == "N0TE-SUPERVISOR", "automation registry must root at N0TE-SUPERVISOR")
    runtime_contract = automation.get("runtime_state_contract")
    require(isinstance(runtime_contract, dict), "automation registry lacks runtime-state ownership contract")
    require(runtime_contract.get("registry_is_runtime_source") is False, "automation registry cannot own live runtime state")
    require(runtime_contract.get("construction_lifecycle_source") == "governance/current_state.json", "construction lifecycle must be owned by current_state")
    require(runtime_contract.get("external_liveness_requires_runtime_observation") is True, "external automation liveness must require runtime observation")
    actors = automation.get("actors", [])
    actor_ids = [row.get("id") for row in actors]
    require(actor_ids and len(actor_ids) == len(set(actor_ids)), "automation actors need unique stable IDs")
    for actor in actors:
        require(actor.get("parent") == "N0TE-SUPERVISOR", f"automation {actor.get('id')} escaped supervision parent")
        require(actor.get("purpose"), f"automation {actor.get('id')} lacks purpose")
        require(actor.get("wake_condition"), f"automation {actor.get('id')} lacks wake condition")
        require(actor.get("retirement_condition"), f"automation {actor.get('id')} lacks retirement condition")
        require(actor.get("observability", {}).get("exact_head_required") is True, f"automation {actor.get('id')} lacks exact-head observability")
        require(actor.get("observability", {}).get("reactivation_is_event") is True, f"automation {actor.get('id')} may reactivate silently")
        require(actor.get("lifecycle", {}).get("state") in {"ACTIVE","DORMANT","RETIRED","QUARANTINED"}, f"automation {actor.get('id')} lacks lifecycle state")
    construction_actor = next((actor for actor in actors if actor.get("id") == "AUTO-CONSTRUCTION-CONTROLLER-001"), None)
    require(construction_actor is not None, "construction controller is not registered")
    require(construction_actor.get("runtime_state_source") == "REPOSITORY_GOVERNANCE_STATE", "construction controller lost repository governance state binding")
    require(construction_actor.get("lifecycle", {}).get("health") == "DERIVED_FROM_CURRENT_STATE", "construction controller lifecycle must remain derived from current_state")

    handoff = load_json(repo / "governance/handoff.json")
    require(handoff.get("repository") == current.get("repository") == "syrustkira/N0TE2", "handoff repository mismatch")
    delivery = handoff.get("delivery", {})
    require(delivery.get("type") == "pull_request" and int(delivery.get("number", 0)) > 0, "handoff lacks tracked delivery object")
    lifecycle = handoff.get("lifecycle")
    if lifecycle is not None:
        require(isinstance(lifecycle, dict), "handoff lifecycle compatibility input must be an object")
        for key, label in (("state", "lifecycle"), ("active_node", "active node"), ("active_increment", "active increment")):
            if key in lifecycle:
                require(lifecycle.get(key) == current.get("lifecycle_state" if key == "state" else key), f"handoff {label} is stale")
    reconstruction = handoff.get("reconstruction", {})
    require(reconstruction.get("handoff_first") is True, "handoff must be first reconstruction surface")
    fallback = reconstruction.get("archaeology_fallback", {})
    require(fallback.get("normal_startup") is False, "historical archaeology cannot be normal startup")
    require(fallback.get("allowed_only_for") == ["MISSING_DURABLE_AUTHORITY", "CONTRADICTORY_DURABLE_AUTHORITY"], "archaeology fallback widened")
    refs = reconstruction.get("required_refs", [])
    missing = [rel for rel in refs if not (repo / rel).exists()]
    require(not missing, f"handoff references missing authority: {', '.join(missing)}")

    merge = load_json(repo / "governance/merge_policy.json")
    required_contexts = {"n0te2-governance-Linux", "n0te2-governance-Windows", "n0te2-governance-macOS"}
    require(set(merge.get("required_exact_head_status_contexts", [])) == required_contexts, "merge policy lost cross-platform exact-head contexts")
    require(merge.get("requirements", {}).get("blocking_incidents_resolved_before_merge") is True, "merge policy permits blocking incidents")
    require(merge.get("external_enforcement", {}).get("repository_file_cannot_prevent_direct_push_by_itself") is True, "merge policy overclaims repository-file enforcement")


def run(repo: Path, verify_git: bool):
    requirements = check_requirements(repo)
    graph = check_graph(repo, requirements)
    check_platforms(repo)
    check_plugins(repo)
    check_authority(repo)
    current = check_stage(repo, graph)
    check_retention_surfaces(repo, current)
    if current["lifecycle_state"] == "ACTIVE":
        check_receipt(repo, verify_git, current)
    print("N0TE2 GOVERNANCE: GREEN")
    print(f"requirements={len(requirements)} nodes={len(graph)} lifecycle={current['lifecycle_state']} active={current.get('active_node') or '-'} increment={current.get('active_increment') or '-'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--no-git", action="store_true")
    args = parser.parse_args()
    try:
        run(Path(args.repo).resolve(), verify_git=not args.no_git)
    except GovernanceError as exc:
        print(f"N0TE2 GOVERNANCE: RED: {exc}", file=sys.stderr)
        sys.exit(1)
