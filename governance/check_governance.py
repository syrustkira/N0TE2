#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path

HEX40 = re.compile(r"^[0-9a-f]{40}$")
REQ_ID = re.compile(r"^REQ-SCOPE-\d{3}$")


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


def require(cond: bool, msg: str):
    if not cond:
        raise GovernanceError(msg)


def check_constitution(repo: Path):
    c = load_json(repo / "governance/constitution.json")
    one = c["one_n0te"]
    laws = c["laws"]
    not_n0te = c["not_n0te"]
    require(one["single_identity"] is True, "N0TE identity must remain singular")
    require(one["persistent_identity_state_reasoning_decision_contract"] is True, "persistent N0TE contract missing")
    require(one["remembers_across_interruptions"] is True, "N0TE continuity memory missing")
    require(one["understands_artist_song_project_and_current_context"] is True, "artist/Song/project context contract missing")
    require(one["consults_durable_memory"] is True, "durable memory consultation contract missing")
    require(one["acts_through_interchangeable_providers"] is True, "provider interchangeability contract missing")
    require(one["observes_and_verifies_outcomes"] is True, "outcome observation/verification contract missing")
    require(one["journals_outcomes_into_memory"] is True, "outcome-to-memory contract missing")
    require(one["resumes_without_manual_reconstruction"] is True, "resume-without-reconstruction contract missing")
    require(one["survives_provider_runtime_and_substrate_replacement"] is True, "substrate survival contract missing")
    require(set(not_n0te) == {"builder", "cloud_controller", "drive_brain", "github", "chatgpt_session", "daw"}, "N0TE substrate boundary set changed")
    require(laws["builder_must_not_become_product_identity"] is True, "builder became N0TE identity")
    require(laws["builder_must_not_own_artist_memory"] is True, "builder became artist-memory owner")
    require(laws["builder_must_not_own_product_semantics"] is True, "builder became product-semantics owner")
    require(laws["accepted_scope_must_be_machine_retained"] is True, "accepted scope may fall out of machine retention")
    require(laws["accepted_scope_must_not_exist_only_in_conversation"] is True, "conversation-only accepted scope allowed")
    require(laws["semantic_graph_must_remain_whole_while_implementation_is_dependency_ordered"] is True, "semantic completeness was confused with implementation order")
    heartbeat = c["heartbeat_contract"]
    expected_heartbeat = [
        "WAKE",
        "IDENTIFY_ARTIST",
        "RESOLVE_CURRENT_SONG_PROJECT_CONTEXT",
        "CONSULT_DURABLE_MEMORY",
        "DECIDE_USEFUL_ACTION",
        "RESOLVE_PROVIDER",
        "AUTHORIZE_AND_EXECUTE",
        "OBSERVE_AND_VERIFY",
        "JOURNAL_AND_REMEMBER",
        "RESUME_LATER_WITHOUT_RECONSTRUCTION",
    ]
    require(heartbeat == expected_heartbeat, "N0TE heartbeat contract changed")
    return c


def check_requirements(repo: Path):
    doc = load_json(repo / "governance/requirements.json")
    start, end = doc["sequence"]["start"], doc["sequence"]["end"]
    require((start, end) == (2, 160), "requirement sequence must be exactly REQ-SCOPE-002..160")
    ids = [f"REQ-SCOPE-{n:03d}" for n in range(start, end + 1)]
    held = set(doc.get("held_or_boundary", []))
    superseded = set(doc.get("superseded", []))
    require(not (held & superseded), "requirement cannot be both held and superseded")
    require((held | superseded).issubset(ids), "non-active requirement outside canonical sequence")
    require(doc.get("default_classification") == "ACTIVE", "default requirement classification must be ACTIVE")
    require(doc.get("active_blocks_candidate") is True, "active requirements must block candidate")
    require(doc.get("non_active_blocks_candidate") is False, "non-active requirements cannot block candidate")
    rows = {}
    for rid in ids:
        require(REQ_ID.match(rid) is not None, f"bad requirement id: {rid}")
        classification = "HELD_OR_BOUNDARY" if rid in held else "SUPERSEDED" if rid in superseded else "ACTIVE"
        rows[rid] = {"id": rid, "classification": classification, "blocks_candidate": classification == "ACTIVE"}
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
    orphan = [rid for rid, row in requirements.items() if row["classification"] == "ACTIVE" and rid not in mapped]
    require(not orphan, f"orphan active requirements: {', '.join(orphan)}")

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
    require(platform_gate["dependency_mode"] == "ALL", "PLATFORM-TEST-READY must be ALL")
    require(set(platform_gate["depends_on"]) == {"PLATFORM-00", "PLATFORM-01", "PLATFORM-02", "PLATFORM-03"}, "platform gate must require macOS+Windows+Linux")
    cand = by_id["CAND-01"]
    require(set(cand["depends_on"]) == {"CONV-01", "DAW-TEST-READY", "PLATFORM-TEST-READY"}, "CAND-01 gate narrowed")
    require(by_id["TEST-01"]["depends_on"] == ["CAND-01"], "TEST-01 must never precede CAND-01")
    require({"REQ-SCOPE-148", "REQ-SCOPE-150", "REQ-SCOPE-160"}.issubset(set(expand_requirement_expr(by_id["DAW-07"]["requirements"]))), "DAW-07 lost Generic Other/plugin/negotiated-interoperability baseline")
    require("AUDIO-02" in by_id["DAW-07"]["depends_on"], "DAW-07 flattened to manual-only: AUDIO-02 missing")
    require({f"REQ-SCOPE-{n:03d}" for n in range(154, 161)}.issubset(set(expand_requirement_expr(by_id["CONV-01"]["requirements"]))), "CONV-01 dropped recovered canonical requirements 154..160")
    require({f"REQ-SCOPE-{n:03d}" for n in range(154, 161)}.issubset(set(expand_requirement_expr(by_id["CAND-01"]["requirements"]))), "candidate gate dropped recovered canonical requirements 154..160")

    active_nodes = [n["id"] for n in nodes if n["state"] == "ACTIVE"]
    require(len(active_nodes) == 1, f"exactly one graph node must be ACTIVE, found {active_nodes}")
    return by_id


def check_platforms(repo: Path):
    p = load_json(repo / "governance/platform_support.json")
    policy = p["policy"]
    require(policy["core_platforms"] == ["macOS", "Windows", "Linux"], "core platform parity must be macOS+Windows+Linux")
    require(policy["version_name_alone_is_breakpoint"] is False, "OS marketing name cannot be a support breakpoint")
    require(policy["unsupported_requires_named_break"] is True, "unsupported must require a named break")
    expected = {
        "macOS": {"arm64", "x86_64"},
        "Windows": {"x86_64", "arm64"},
        "Linux": {"x86_64", "aarch64"},
    }
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
    require("governance/constitution.json" in a["current_authority_files"], "one-N0TE constitution missing from repo authority surface")
    require("governance/legacy_admission.json" in a["current_authority_files"], "LEGACY-01 admission manifest missing from repo authority surface")
    require(a["laws"]["implementation_maturity_must_not_mutate_scope"] is True, "anti-flattening law missing")
    require(a["laws"]["missing_acceptance_resource_stops_unrelated_construction"] is False, "resource-wait loop reintroduced")
    require(a["laws"]["legacy_classification_is_not_copy_authority"] is True, "classification became copy authority")
    require(a["laws"]["legacy_test_green_is_not_product_completion"] is True, "legacy tests became product-completion authority")
    require(a["laws"]["one_n0te_identity_must_survive_substrate_replacement"] is True, "one-N0TE substrate survival law missing")
    require(a["laws"]["builder_is_not_product_identity"] is True, "builder/product identity boundary missing")
    require(a["laws"]["accepted_scope_must_be_machine_retained"] is True, "scope retention law missing")
    require(a["laws"]["conversation_only_accepted_scope_is_forbidden"] is True, "conversation-only product scope allowed")


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
    required_gaps = {
        "CONNECTED_TO_OFFLINE_PENDING_STATE_CHOICE",
        "OFFLINE_TO_CONNECTED_EXPLICIT_RECONCILIATION_CHOICE",
        "PRIVATE_DATA_EGRESS_EXACT_DATA_DESTINATION_PURPOSE",
        "CORRUPTION_VISIBILITY_AND_RECOVERY",
    }
    require(required_gaps.issubset(set(evidence["n0te2_replacement_regression_gaps"])), "N0TE2 replacement regression gaps were dropped")
    required_contamination = {
        "ABLETON_SEMANTIC_PRIORITY",
        "MANUAL_FALLBACK_AS_PRODUCT_IDENTITY",
        "DEEPEST_ADAPTER_OR_FIXED_TIER_RANKING",
        "DUPLICATE_PRODUCT_OR_COMPLETION_AUTHORITY",
        "PARALLEL_STATE_STORE_SPIDERWEB",
        "MONOLITHIC_SERVER_OR_ROUTE_MONKEY_PATCHING",
        "PLAINTEXT_PERSISTENT_SECRET_FALLBACK",
        "SILENT_CORRUPTION_TO_EMPTY_STATE",
        "SILENT_FIXED_HISTORY_TRUNCATION",
        "HISTORICAL_PLATFORM_OR_VERSION_FLOORS_AS_SCOPE",
        "GENERIC_NAMES_HIDING_HOST_SPECIFIC_IMPLEMENTATION",
    }
    require(required_contamination.issubset(set(p["contamination_classes"])), "required contamination classes were dropped")
    require(all(p["admission_law"].values()), "legacy admission law weakened")
    return p


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT).strip()


def path_allowed(path: str, receipt: dict) -> bool:
    if path in set(receipt.get("allowed_exact_paths", [])):
        return True
    return any(path.startswith(prefix) for prefix in receipt.get("allowed_prefixes", []))


def check_receipt(repo: Path, verify_git: bool, current: dict):
    receipt = load_json(repo / "governance/active_receipt.json")
    active = current["active_node"]
    if active == "BOOT-02":
        expected_receipt = "N0TE2-BOOT-02"
    elif active == "LEGACY-01":
        expected_receipt = "N0TE2-LEGACY-01"
    else:
        increment = str(current.get("active_increment") or "").strip()
        require(bool(increment), f"{active} product construction requires an active_increment")
        expected_receipt = f"N0TE2-{increment}"
        require(receipt.get("increment_id") == increment, "receipt increment must match current active_increment")
        require(increment.startswith(active), f"active increment {increment} must belong to active node {active}")

    require(receipt["receipt_id"] == expected_receipt, "unexpected active receipt")
    require(receipt["node_id"] == active, "receipt must bind current active node")
    baseline = receipt["baseline_sha"]
    require(HEX40.match(baseline) is not None, "receipt baseline_sha must be exact 40-char lowercase SHA")

    if active == "BOOT-02":
        require(receipt["product_code_allowed"] is False, "BOOT-02 receipt cannot allow product code")
        require(receipt["legacy_admission_allowed"] is False, "BOOT-02 cannot authorize legacy admission")
    elif active == "LEGACY-01":
        require(receipt["product_code_allowed"] is False, "LEGACY-01 receipt cannot allow product code")
        require(receipt["legacy_admission_allowed"] is True, "LEGACY-01 must authorize migration evidence work")
        require(receipt["legacy_source_copy_allowed"] is False, "LEGACY-01 cannot authorize direct legacy source copy")
        require(receipt["legacy_test_text_copy_allowed"] is False, "LEGACY-01 cannot authorize direct legacy test-text copy")
    else:
        require(receipt["product_code_allowed"] is True, f"{active} product increment must explicitly authorize bounded product code")
        require(receipt["legacy_admission_allowed"] is False, f"{active} cannot reopen legacy admission")
        require(receipt["legacy_source_copy_allowed"] is False, f"{active} cannot authorize direct legacy source copy")
        require(receipt["legacy_test_text_copy_allowed"] is False, f"{active} cannot authorize direct legacy test-text copy")
        require(bool(receipt.get("allowed_prefixes")), "product receipt must define bounded allowed paths")
        if active == "CORE-01":
            require(
                {"n0te2/", "tests/core/", "governance/", "tests/governance/", ".github/workflows/"}.issubset(set(receipt.get("allowed_prefixes", []))),
                "CORE-01 receipt lost bounded implementation/test/governance paths",
            )
            require(
                {"app/", "src/", "legacy/", "vendor/"}.issubset(set(receipt.get("forbidden_prefixes", []))),
                "CORE-01 receipt weakened clean-room forbidden paths",
            )

    if verify_git:
        try:
            inside = git(repo, "rev-parse", "--is-inside-work-tree") == "true"
        except Exception as exc:
            raise GovernanceError(f"git verification requested but repo unavailable: {exc}") from exc
        require(inside, "not a git worktree")
        try:
            git(repo, "merge-base", "--is-ancestor", baseline, "HEAD")
        except subprocess.CalledProcessError as exc:
            raise GovernanceError(f"receipt baseline {baseline} is not an ancestor of HEAD: {exc.output}") from exc
        changed = [path for path in git(repo, "diff", "--name-only", f"{baseline}...HEAD").splitlines() if path]
        bad = [path for path in changed if not path_allowed(path, receipt)]
        require(not bad, f"changed paths outside {active} receipt: {', '.join(bad)}")
        forbidden = tuple(receipt.get("forbidden_prefixes", []))
        bad_forbidden = [path for path in changed if path.startswith(forbidden)]
        require(not bad_forbidden, f"forbidden clean-room paths changed during {active}: {', '.join(bad_forbidden)}")
    return receipt


def check_stage(repo: Path, graph: dict):
    current = load_json(repo / "governance/current_state.json")
    active = current["active_node"]
    held = load_json(repo / "governance/held_scope.json")
    held_ids = {item["id"] for item in held["items"]}
    require(active not in held_ids, "held item became active")
    require(held_ids == {f"HOLD-{n:03d}" for n in range(1, 8)}, "held scope changed without explicit promotion")
    require(active in graph, "current active node is not in completion graph")
    require(graph[active]["state"] == "ACTIVE", "current active node must be ACTIVE in graph")
    require([node for node, row in graph.items() if row["state"] == "ACTIVE"] == [active], "graph/current-state active node mismatch")
    for done in current.get("completed_nodes", []):
        require(done in graph and graph[done]["state"] == "DONE", f"completed node not DONE in graph: {done}")

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
        increment = str(current.get("active_increment") or "").strip()
        require(bool(increment), f"{active} requires a bounded active increment")
        require(increment.startswith(active), f"active increment {increment} must belong to active node {active}")
        check_legacy_admission(repo)
    return current


def run(repo: Path, verify_git: bool):
    check_constitution(repo)
    requirements = check_requirements(repo)
    graph = check_graph(repo, requirements)
    check_platforms(repo)
    check_plugins(repo)
    check_authority(repo)
    current = check_stage(repo, graph)
    check_receipt(repo, verify_git, current)
    print("N0TE2 GOVERNANCE: GREEN")
    print(f"requirements={len(requirements)} nodes={len(graph)} active={current['active_node']} increment={current.get('active_increment', '-')}")


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
