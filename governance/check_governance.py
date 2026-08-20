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

def check_requirements(repo: Path):
    doc = load_json(repo / "governance/requirements.json")
    seq = doc["sequence"]
    start, end = seq["start"], seq["end"]
    require((start, end) == (2, 153), "requirement sequence must be exactly REQ-SCOPE-002..153")
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
    doc = load_json(repo / "governance/completion_graph.json")
    nodes = doc["nodes"]
    ids = [n["id"] for n in nodes]
    require(len(ids) == 48, f"expected 48 completion nodes, found {len(ids)}")
    require(len(ids) == len(set(ids)), "duplicate graph node IDs")
    by_id = {n["id"]: n for n in nodes}

    for n in nodes:
        require(n["autonomy"] in {"GREEN","AMBER","RED"}, f"bad autonomy: {n['id']}")
        require(n["state"] in {"DONE","ACTIVE","PRESERVED","LATER"}, f"bad state: {n['id']}")
        require(n["dependency_mode"] in {"ROOT","ALL","ALL+ANY"}, f"bad dependency mode: {n['id']}")
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
    q = deque([nid for nid, deg in indegree.items() if deg == 0])
    seen = []
    while q:
        cur = q.popleft(); seen.append(cur)
        for child in children[cur]:
            indegree[child] -= 1
            if indegree[child] == 0:
                q.append(child)
    require(len(seen) == len(ids), "completion graph contains a dependency cycle")

    mapped = set()
    for n in nodes:
        if n["required"]:
            mapped.update(expand_requirement_expr(n.get("requirements", "")))
    orphan = [rid for rid, r in requirements.items() if r["classification"] == "ACTIVE" and rid not in mapped]
    require(not orphan, f"orphan active requirements: {', '.join(orphan)}")

    later = by_id["LATER-01"]
    held = {rid for rid, r in requirements.items() if r["classification"] == "HELD_OR_BOUNDARY"}
    require(held.issubset(set(expand_requirement_expr(later.get("requirements", "")))), "LATER-01 does not preserve every held/boundary requirement")
    superseded = {rid for rid, r in requirements.items() if r["classification"] == "SUPERSEDED"}
    require(not (superseded & mapped), "superseded requirement leaked into required candidate coverage")

    six = {f"DAW-0{i}" for i in range(1,7)}
    daw_gate = by_id["DAW-TEST-READY"]
    require(daw_gate["dependency_mode"] == "ALL", "DAW-TEST-READY must be ALL")
    require(set(daw_gate["depends_on"]) == ({"DAW-00","DAW-07"} | six), "DAW-TEST-READY must require DAW-00, all six core DAWs, and DAW-07")
    platform_gate = by_id["PLATFORM-TEST-READY"]
    require(platform_gate["dependency_mode"] == "ALL", "PLATFORM-TEST-READY must be ALL")
    require(set(platform_gate["depends_on"]) == {"PLATFORM-00","PLATFORM-01","PLATFORM-02","PLATFORM-03"}, "platform gate must require macOS+Windows+Linux")
    cand = by_id["CAND-01"]
    require(set(cand["depends_on"]) == {"CONV-01","DAW-TEST-READY","PLATFORM-TEST-READY"}, "CAND-01 gate narrowed")
    require(by_id["TEST-01"]["depends_on"] == ["CAND-01"], "TEST-01 must never precede CAND-01")
    require({"REQ-SCOPE-148","REQ-SCOPE-150"}.issubset(set(expand_requirement_expr(by_id["DAW-07"]["requirements"]))), "DAW-07 lost Generic Other/plugin baseline")
    require("AUDIO-02" in by_id["DAW-07"]["depends_on"], "DAW-07 flattened to manual-only: AUDIO-02 missing")
    return by_id

def check_platforms(repo: Path):
    p = load_json(repo / "governance/platform_support.json")
    policy = p["policy"]
    require(policy["core_platforms"] == ["macOS","Windows","Linux"], "core platform parity must be macOS+Windows+Linux")
    require(policy["version_name_alone_is_breakpoint"] is False, "OS marketing name cannot be a support breakpoint")
    require(policy["unsupported_requires_named_break"] is True, "unsupported must require a named break")
    expected = {
        "macOS": {"arm64","x86_64"},
        "Windows": {"x86_64","arm64"},
        "Linux": {"x86_64","aarch64"},
    }
    actual = {k:set(v) for k,v in p["core_architectures"].items()}
    require(actual == expected, f"core architecture matrix narrowed: {actual}")
    linux = p["linux_packaging"]
    require({"AppImage","tar.zst"}.issubset(set(linux["broad_baseline"])), "Linux portable baseline missing")
    require({"deb","rpm"}.issubset(set(linux["native"])), "Linux native package families missing")
    return p

def check_plugins(repo: Path):
    p = load_json(repo / "governance/plugin_contract.json")
    require(p["universal_n0te_capability"] is True, "plugin capability cannot belong to one DAW")
    require(p["scan_standard_locations"] is True, "standard plugin paths must be scanned")
    require(p["ask_for_additional_locations"] is True, "must ask for additional plugin locations")
    require(p["persist_custom_locations"] is True, "custom plugin paths must persist")
    require(p["silent_arbitrary_disk_crawl"] is False, "silent whole-disk crawl forbidden")
    required_formats = {"VST3","AU","CLAP","LV2","LADSPA","AAX"}
    require(required_formats.issubset(set(p["formats"])), "plugin format model flattened")
    require(p["formats"]["AAX"]["standalone_direct_host_claim"] is False, "AAX standalone hosting must not be claimed without proof")
    require(p["prefer_out_of_process_architecture_specific_workers"] is True, "plugin workers must preserve architecture isolation preference")

def check_held(repo: Path, graph):
    h = load_json(repo / "governance/held_scope.json")
    ids = {i["id"] for i in h["items"]}
    require(ids == {f"HOLD-{n:03d}" for n in range(1,8)}, "held scope changed without explicit promotion")
    current = load_json(repo / "governance/current_state.json")
    require(current["active_node"] not in ids, "held item became active")
    require(current["product_code_authorized"] is False, "BOOT-02 must not authorize product code")
    require(current["legacy_admission_authorized"] is False, "BOOT-02 must not authorize legacy admission")
    require(graph["BOOT-02"]["state"] == "ACTIVE", "BOOT-02 must remain active in this receipt")

def check_authority(repo: Path):
    a = load_json(repo / "governance/authority.json")
    joined = "\n".join(a["current_authority_files"])
    for marker in a["forbidden_current_authority_markers"]:
        require(marker not in joined, f"stale authority reintroduced: {marker}")
    require(a["laws"]["implementation_maturity_must_not_mutate_scope"] is True, "anti-flattening law missing")
    require(a["laws"]["missing_acceptance_resource_stops_unrelated_construction"] is False, "resource-wait loop reintroduced")

def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT).strip()

def path_allowed(path: str, receipt: dict) -> bool:
    exact = set(receipt.get("allowed_exact_paths", []))
    if path in exact:
        return True
    return any(path.startswith(prefix) for prefix in receipt.get("allowed_prefixes", []))

def check_receipt(repo: Path, verify_git: bool):
    r = load_json(repo / "governance/active_receipt.json")
    require(r["receipt_id"] == "N0TE2-BOOT-02", "unexpected active receipt")
    require(r["node_id"] == "BOOT-02", "receipt must bind BOOT-02")
    baseline = r["baseline_sha"]
    require(HEX40.match(baseline) is not None, "receipt baseline_sha must be exact 40-char lowercase SHA")
    require(r["product_code_allowed"] is False, "BOOT-02 receipt cannot allow product code")
    require(r["legacy_admission_allowed"] is False, "BOOT-02 receipt cannot allow legacy admission")
    if verify_git:
        try:
            inside = git(repo, "rev-parse", "--is-inside-work-tree") == "true"
        except Exception as exc:
            raise GovernanceError(f"git verification requested but repo unavailable: {exc}")
        require(inside, "not a git worktree")
        try:
            git(repo, "merge-base", "--is-ancestor", baseline, "HEAD")
        except subprocess.CalledProcessError as exc:
            raise GovernanceError(f"receipt baseline {baseline} is not an ancestor of HEAD: {exc.output}") from exc
        changed = [p for p in git(repo, "diff", "--name-only", f"{baseline}...HEAD").splitlines() if p]
        bad = [p for p in changed if not path_allowed(p, r)]
        require(not bad, f"changed paths outside BOOT-02 receipt: {', '.join(bad)}")
        forbidden = tuple(r.get("forbidden_prefixes", []))
        bad_forbidden = [p for p in changed if p.startswith(forbidden)]
        require(not bad_forbidden, f"product/legacy paths changed during BOOT-02: {', '.join(bad_forbidden)}")
    return r

def run(repo: Path, verify_git: bool):
    requirements = check_requirements(repo)
    graph = check_graph(repo, requirements)
    check_platforms(repo)
    check_plugins(repo)
    check_held(repo, graph)
    check_authority(repo)
    check_receipt(repo, verify_git)
    print("N0TE2 GOVERNANCE: GREEN")
    print(f"requirements={len(requirements)} nodes={len(graph)} active=BOOT-02")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--no-git", action="store_true")
    args = ap.parse_args()
    try:
        run(Path(args.repo).resolve(), verify_git=not args.no_git)
    except GovernanceError as exc:
        print(f"N0TE2 GOVERNANCE: RED: {exc}", file=sys.stderr)
        sys.exit(1)
