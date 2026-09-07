#!/usr/bin/env python3
from __future__ import annotations

import argparse, copy, datetime as dt, json, re, subprocess, sys
from pathlib import Path
from typing import Any

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HV = re.compile(r"^H(0|[1-9][0-9]*)$")
RISKS = {"TIER_0", "TIER_1", "TIER_2", "TIER_3"}
PUBLIC = {"PUBLIC_IMPACT_NONE", "PUBLIC_IMPACT_PRESENT"}
LENS = {"APPLICABLE_PASS", "APPLICABLE_FINDING", "NOT_APPLICABLE_WITH_REASON", "STEWARD_REVIEW_REQUIRED", "EXTERNAL_ACCEPTANCE_REQUIRED"}
ORDERS = {"FIX_ORDER", "REBUILD_ORDER", "SPLIT_ORDER"}
FORBIDDEN_STATES = {"MERGED_VERIFIED", "PUBLIC_VERIFIED", "CONSUMER_ACCEPTED", "VALUE_EVIDENCED", "HUMAN_ACCEPTED", "RIGHTS_CLEARED", "STEWARD_APPROVED"}
RESERVED_KEYS = {"merge_authorized", "steward_approved", "human_accepted", "public_accepted", "rights_cleared", "consumer_accepted", "value_evidenced"}
RIGHTS_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".tif", ".tiff", ".wav", ".aif", ".aiff", ".mp3", ".flac", ".m4a", ".ogg", ".ttf", ".otf", ".woff", ".woff2"}
REQ_SECTIONS = {"identity", "bounded_increment", "semantic_contract", "behavior", "dependencies", "validation", "review", "risk", "public_consequence", "limitations", "steward_handoff", "lenses", "lineage"}
TEST_FIELDS = {"command", "environment", "exact_head", "result", "observed_at", "artifact_refs"}
DEFER_FIELDS = {"description", "reason", "dependency", "owner", "impact", "merge_may_proceed", "future_acceptance_condition", "successor_work_item", "durable_state"}
RIGHTS_FIELDS = {"subject", "source", "license_or_rights_basis", "provenance", "modifications", "evidence_refs", "unresolved_questions"}


class BuilderHandoffError(RuntimeError): pass

def now() -> str: return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
def load(path: Path) -> dict[str, Any]:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc: raise BuilderHandoffError(f"cannot load JSON {path}: {exc}") from exc
    if not isinstance(value, dict): raise BuilderHandoffError(f"{path} must contain a JSON object")
    return value
def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
def git(repo: Path, *args: str) -> str:
    try: return subprocess.check_output(["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc: raise BuilderHandoffError(f"git {' '.join(args)} failed: {exc.output.strip()}") from exc
def lines(repo: Path, *args: str) -> list[str]: return [x for x in git(repo, *args).splitlines() if x.strip()]
def text(v: Any) -> bool: return isinstance(v, str) and bool(v.strip())

def walk(v: Any, p: str = ""):
    if isinstance(v, dict):
        for k, x in v.items():
            q = f"{p}.{k}" if p else k; yield q, x; yield from walk(x, q)
    elif isinstance(v, list):
        for i, x in enumerate(v): yield from walk(x, f"{p}[{i}]")

def hints(files: list[str]) -> dict[str, Any]:
    low = [p.casefold() for p in files]
    return {
        "rights_or_provenance_files": [p for p in files if Path(p).suffix.casefold() in RIGHTS_EXT],
        "migration_or_schema_files": [p for p, q in zip(files, low) if "migration" in q or "migrations/" in q or "schema" in q or q.endswith(".sql")],
        "high_consequence_files": [p for p, q in zip(files, low) if q.startswith(".github/workflows/") or q in {"governance/authority.json", "governance/merge_policy.json", "governance/check_steward_integration.py"} or any(x in q for x in ("security", "privacy", "rights", "migration"))],
    }
def relationship(repo: Path, base: str, main: str, head: str) -> dict[str, Any]:
    out = {"baseline_is_current_main": base == main, "head_equals_main": head == main, "intervening_main_commits": []}
    for key, span in (("baseline", f"{base}...{head}"), ("main", f"{head}...{main}")):
        try:
            left, right = [int(x) for x in git(repo, "rev-list", "--left-right", "--count", span).split()]
            out[f"behind_{key}"] = left; out[f"ahead_of_{key}"] = right
        except Exception: out[f"behind_{key}"] = out[f"ahead_of_{key}"] = None
    try: out["intervening_main_commits"] = lines(repo, "log", "--format=%H", f"{head}..{main}")[:50]
    except Exception: pass
    return out
def collect(repo: Path, base: str, main: str, base_branch: str = "main", pr: str | None = None) -> dict[str, Any]:
    repo = repo.resolve(); head = git(repo, "rev-parse", "HEAD"); branch = git(repo, "branch", "--show-current") or "DETACHED_HEAD"
    if branch == "main": raise BuilderHandoffError("Builder handoff sealing refuses to run from main; Builders hand off candidate heads")
    for name, sha in (("baseline_sha", base), ("current_main_sha", main), ("head_sha", head)):
        if not HEX40.fullmatch(sha): raise BuilderHandoffError(f"{name} must be a lowercase 40-character Git SHA")
    files = lines(repo, "diff", "--name-only", f"{base}...{head}")
    return {
        "baseline": {"baseline_branch": base_branch, "baseline_sha": base, "current_main_sha": main, "relationship_to_current_main": relationship(repo, base, main, head), "refreshed_at": now()},
        "candidate": {"branch": branch, "pr": pr, "head_sha": head, "commits": lines(repo, "log", "--format=%H", f"{base}..{head}"), "changed_files": files, "generated_files": [p for p in files if "generated/" in p or p.endswith(".generated.json")]},
        "capture": {"mode": "RUNTIME_EXACT", "captured_at": now(), "git_command": "git rev-parse HEAD", "manifest_commit_rule": "sealed manifest is runtime evidence and does not live inside the candidate commit it identifies"},
        "hints": hints(files),
    }
def split_signal(b: dict[str, Any]) -> tuple[str, list[str]]:
    x = b.get("bounded_increment", {}); req = []; rec = []
    if x.get("cohesive") is False: req.append("increment declared non-cohesive")
    if x.get("unrelated_concerns") is True: req.append("unrelated concerns bundled")
    if x.get("partial_failure_blocks_unrelated") is True: req.append("partial failure blocks unrelated work")
    for k, msg in (("mixed_risk_surfaces", "risk surfaces differ materially"), ("independent_disposition_possible", "parts can be dispositioned independently"), ("external_dependency_partial", "one part waits on external state"), ("review_opaque", "review is unreasonably opaque")):
        if x.get(k) is True: rec.append(msg)
    return ("SPLIT_REQUIRED", req) if req else (("SPLIT_RECOMMENDED", rec) if rec else ("NO_SPLIT_SIGNAL", []))
def validate(m: dict[str, Any], current_head: str | None = None) -> list[str]:
    f: list[str] = []
    if m.get("kind") != "N0TE_BUILDER_HANDOFF": f.append("kind must be N0TE_BUILDER_HANDOFF")
    ver = m.get("handoff_version")
    if not isinstance(ver, str) or not HV.fullmatch(ver): f.append("handoff_version must be H0, H1, H2, ...")
    b = m.get("builder_assertions")
    if not isinstance(b, dict): return f + ["builder_assertions must be an object"]
    missing = sorted(REQ_SECTIONS - set(b))
    if missing: f.append("missing builder sections: " + ", ".join(missing))
    ident = b.get("identity", {})
    for k in ("builder_id", "candidate_id", "product_outcome", "artist_job", "semantic_owner"):
        if not text(ident.get(k)): f.append(f"missing or empty text: builder_assertions.identity.{k}")
    if not isinstance(ident.get("requirement_ids"), list) or not ident.get("requirement_ids"): f.append("identity.requirement_ids must be a non-empty list")
    if ident.get("candidate_id") != m.get("candidate_id"): f.append("candidate_id must match identity.candidate_id")
    bounded = b.get("bounded_increment", {})
    for k in ("increment_id", "description"):
        if not text(bounded.get(k)): f.append(f"missing bounded_increment.{k}")
    if type(bounded.get("cohesive")) is not bool: f.append("bounded_increment.cohesive must be boolean")
    sem = b.get("semantic_contract", {})
    for k in ("governing_requirement", "required_behavior"):
        if not text(sem.get(k)): f.append(f"missing semantic_contract.{k}")
    if not isinstance(sem.get("non_negotiable_semantics"), list) or not sem.get("non_negotiable_semantics"): f.append("semantic_contract.non_negotiable_semantics must be non-empty")
    for k in ("allowed_implementation_freedom", "interpretations_required", "remaining_semantics"):
        if not isinstance(sem.get(k), list): f.append(f"semantic_contract.{k} must be a list")
    beh = b.get("behavior", {})
    for k in ("requested_behavior", "implemented_behavior"):
        if not text(beh.get(k)): f.append(f"missing behavior.{k}")
    cp = beh.get("consumer_path", {})
    if type(cp.get("applicable")) is not bool: f.append("behavior.consumer_path.applicable must be boolean")
    elif cp.get("applicable") and (not text(cp.get("path")) or not text(cp.get("proof"))): f.append("applicable consumer path requires path and proof")
    elif cp.get("applicable") is False and not text(cp.get("reason")): f.append("non-applicable consumer path requires reason")
    deps = b.get("dependencies", {})
    for k in ("upstream", "downstream_consumers", "shared_domains", "shared_files", "ordering_assumptions", "provider_assumptions", "platform_assumptions", "semantic_assumptions", "collision_surfaces"):
        if not isinstance(deps.get(k), list): f.append(f"dependencies.{k} must be a list")
    rt = m.get("runtime_binding", {}); base = rt.get("baseline", {}); cand = rt.get("candidate", {}); head = cand.get("head_sha"); files = cand.get("changed_files", [])
    for p, v in (("baseline_branch", base.get("baseline_branch")), ("baseline_sha", base.get("baseline_sha")), ("current_main_sha", base.get("current_main_sha")), ("branch", cand.get("branch")), ("head_sha", head)):
        if not text(v): f.append(f"runtime {p} missing")
    for p, v in (("baseline_sha", base.get("baseline_sha")), ("current_main_sha", base.get("current_main_sha")), ("head_sha", head)):
        if isinstance(v, str) and not HEX40.fullmatch(v): f.append(f"runtime {p} must be lowercase SHA40")
    for k in ("commits", "changed_files", "generated_files"):
        if not isinstance(cand.get(k), list): f.append(f"runtime candidate.{k} must be a list")
    if cand.get("branch") == "main": f.append("Builder candidate branch may not be main")
    if current_head is not None and head != current_head: f.append(f"exact-head binding is stale: manifest={head} current={current_head}")
    risk = b.get("risk", {}); tier = risk.get("tier")
    if tier not in RISKS: f.append(f"invalid risk tier: {tier!r}")
    for k in ("high_consequence_surfaces", "failure_modes", "rollback_recovery_considerations"):
        if not isinstance(risk.get(k), list): f.append(f"risk.{k} must be a list")
    val = b.get("validation", {}); receipts = val.get("test_receipts", [])
    if not isinstance(receipts, list): f.append("validation.test_receipts must be a list"); receipts = []
    for i, r in enumerate(receipts):
        if not isinstance(r, dict): f.append(f"test receipt {i} must be object"); continue
        miss = sorted(TEST_FIELDS - set(r))
        if miss: f.append(f"test receipt {i} missing fields: {', '.join(miss)}")
        if head and r.get("exact_head") != head: f.append(f"test receipt {i} is not bound to exact candidate head {head}")
        if r.get("result") not in {"PASS", "FAIL", "NOT_RUN", "BLOCKED"}: f.append(f"test receipt {i} invalid result")
        if r.get("result") == "NOT_RUN" and not text(r.get("reason")): f.append(f"test receipt {i} NOT_RUN requires reason")
    if tier != "TIER_0" and tier in RISKS and not receipts: f.append(f"{tier} requires at least one exact-head test receipt")
    if tier in {"TIER_2", "TIER_3"}:
        for k in ("regression_proof", "consumer_smoke"):
            if not text(val.get(k)): f.append(f"validation.{k} required for {tier}")
    if tier == "TIER_3":
        for k in ("full_regression", "recovery_proof"):
            if not text(val.get(k)): f.append(f"validation.{k} required for TIER_3")
    pub = b.get("public_consequence", {}); state = pub.get("classification")
    if state not in PUBLIC: f.append("public impact must be explicitly PUBLIC_IMPACT_NONE or PUBLIC_IMPACT_PRESENT")
    if state == "PUBLIC_IMPACT_PRESENT":
        for k in ("pub_requirement_ids", "public_domains", "public_assets", "providers", "rights_effects", "privacy_security_effects", "accessibility_effects", "deployment_prerequisites", "migration_state_effects", "rollback_considerations", "human_acceptance_needs"):
            if not isinstance(pub.get(k), list): f.append(f"public_consequence.{k} must be a list")
    h = hints(files if isinstance(files, list) else [])
    if h["rights_or_provenance_files"]:
        rights = b.get("rights_provenance")
        if not isinstance(rights, list) or not rights: f.append("rights/provenance-sensitive files changed but rights_provenance is absent or empty")
        else:
            for i, r in enumerate(rights):
                miss = sorted(RIGHTS_FIELDS - set(r)) if isinstance(r, dict) else sorted(RIGHTS_FIELDS)
                if miss: f.append(f"rights_provenance[{i}] missing fields: {', '.join(miss)}")
    if h["migration_or_schema_files"]:
        if not text(val.get("migration_proof")): f.append("schema/migration change detected but migration_proof is absent")
        if not text(val.get("recovery_proof")): f.append("schema/migration change detected but recovery_proof is absent")
    lim = b.get("limitations", {})
    for k in ("known_limitations", "deferred_portions", "external_dependencies", "provider_dependencies", "human_acceptance_needs", "public_acceptance_needs"):
        if not isinstance(lim.get(k), list): f.append(f"limitations.{k} must be a list")
    for i, d in enumerate(lim.get("deferred_portions", []) if isinstance(lim.get("deferred_portions"), list) else []):
        miss = sorted(DEFER_FIELDS - set(d)) if isinstance(d, dict) else sorted(DEFER_FIELDS)
        if miss: f.append(f"deferred_portions[{i}] missing fields: {', '.join(miss)}")
    rev = b.get("review", {})
    if not text(rev.get("status")): f.append("review.status required")
    for k in ("unresolved_findings", "inline_threads", "reviewer_assumptions"):
        if not isinstance(rev.get(k), list): f.append(f"review.{k} must be a list")
    sh = b.get("steward_handoff", {})
    if not text(sh.get("expected_disposition")): f.append("steward_handoff.expected_disposition required")
    for k in ("candidate_frozen_for_qualification", "builder_available_for_fix"):
        if type(sh.get(k)) is not bool: f.append(f"steward_handoff.{k} must be boolean")
    if not isinstance(sh.get("dependency_safe_continuation"), list): f.append("steward_handoff.dependency_safe_continuation must be list")
    if head and sh.get("exact_head_to_review") != head: f.append("steward_handoff.exact_head_to_review must equal runtime exact head")
    lenses = b.get("lenses")
    if not isinstance(lenses, list): f.append("lenses must be list")
    else:
        for i, x in enumerate(lenses):
            if not isinstance(x, dict) or not text(x.get("name")) or x.get("state") not in LENS: f.append(f"lens {i} invalid")
            elif x.get("state") != "APPLICABLE_PASS" and not text(x.get("reason")): f.append(f"lens {i} requires reason")
    for p, v in walk(b):
        key = p.rsplit(".", 1)[-1].split("[", 1)[0]
        if key in RESERVED_KEYS and v not in (False, None, "NOT_EVALUATED"): f.append(f"Builder may not self-grant authority via {p}={v!r}")
        if isinstance(v, str) and v in FORBIDDEN_STATES: f.append(f"Builder may not report authority state {v} at {p}")
    lin = b.get("lineage", {}); order = lin.get("order")
    if order is not None:
        if not isinstance(order, dict) or order.get("type") not in ORDERS: f.append("lineage.order invalid")
        else:
            for k in ("order_id", "source_candidate_id", "source_handoff_version"):
                if not text(order.get(k)): f.append(f"lineage.order.{k} required")
    if isinstance(ver, str) and HV.fullmatch(ver):
        n = int(ver[1:]); prev = lin.get("previous_handoff_version")
        if n == 0 and prev not in (None, ""): f.append("H0 may not have previous_handoff_version")
        if n > 0 and not text(prev): f.append(f"{ver} must preserve previous_handoff_version")
    return f
def seal(declaration: dict[str, Any], runtime: dict[str, Any], expected_head: str | None = None) -> dict[str, Any]:
    b = copy.deepcopy(declaration.get("builder_assertions"))
    if not isinstance(b, dict): raise BuilderHandoffError("declaration must contain builder_assertions object")
    head = runtime["candidate"]["head_sha"]
    if expected_head and head != expected_head: raise BuilderHandoffError(f"candidate head moved: expected {expected_head}, got {head}")
    b.setdefault("steward_handoff", {})["exact_head_to_review"] = head
    for r in b.get("validation", {}).get("test_receipts", []):
        if isinstance(r, dict) and r.get("exact_head") in (None, "RUNTIME_EXACT"): r["exact_head"] = head
    m = {"schema_version": 1, "kind": "N0TE_BUILDER_HANDOFF", "candidate_id": declaration.get("candidate_id") or b.get("identity", {}).get("candidate_id"), "handoff_version": declaration.get("handoff_version", "H0"), "sealed_at": now(), "builder_assertions": b, "runtime_binding": runtime, "authority_verification": {k: {"state": "NOT_EVALUATED", "evidence_refs": []} for k in ("ci", "human_acceptance", "public_acceptance", "rights_clearance")}, "computed": {}}
    m["authority_verification"]["steward"] = {"state": "NOT_EVALUATED", "disposition": None, "evidence_refs": []}
    fs = validate(m, head); split, reasons = split_signal(b); state = "READY_HANDOFF" if not fs else "HANDOFF_BLOCKED"
    if state == "READY_HANDOFF" and split != "NO_SPLIT_SIGNAL": state = split
    m["computed"] = {"state": state, "findings": fs, "split_signal": split, "split_reasons": reasons, "hints": runtime.get("hints", {})}; return m
def successor(previous: dict[str, Any], order_type: str, order_id: str) -> dict[str, Any]:
    if order_type not in ORDERS: raise BuilderHandoffError(f"invalid order type: {order_type}")
    v = previous.get("handoff_version")
    if not isinstance(v, str) or not HV.fullmatch(v): raise BuilderHandoffError("previous handoff_version invalid")
    b = copy.deepcopy(previous.get("builder_assertions", {})); lin = b.setdefault("lineage", {}); lin["previous_handoff_version"] = v; lin["previous_head_sha"] = previous.get("runtime_binding", {}).get("candidate", {}).get("head_sha"); lin["order"] = {"type": order_type, "order_id": order_id, "source_candidate_id": previous.get("candidate_id"), "source_handoff_version": v}
    b.setdefault("validation", {})["test_receipts"] = []; b.setdefault("review", {})["status"] = "REVIEW_REQUIRED_AFTER_ORDER"; b.setdefault("review", {})["unresolved_findings"] = []; b.setdefault("steward_handoff", {})["candidate_frozen_for_qualification"] = False; b["steward_handoff"]["exact_head_to_review"] = "RUNTIME_EXACT"
    return {"schema_version": 1, "kind": "N0TE_BUILDER_HANDOFF_DECLARATION", "candidate_id": previous.get("candidate_id"), "handoff_version": f"H{int(v[1:])+1}", "builder_assertions": b}
def manifests(paths: list[Path]) -> list[Path]:
    out = []
    for p in paths: out += sorted(p.rglob("*.json")) if p.is_dir() else ([p] if p.is_file() else [])
    return out
def intake(paths: list[Path]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}; invalid = []
    for p in manifests(paths):
        try: m = load(p)
        except BuilderHandoffError as exc: invalid.append({"path": str(p), "error": str(exc)}); continue
        if m.get("kind") != "N0TE_BUILDER_HANDOFF": continue
        fs = validate(m)
        if fs: invalid.append({"path": str(p), "candidate_id": m.get("candidate_id"), "findings": fs})
        groups.setdefault(str(m.get("candidate_id")), []).append(m)
    rows = []
    for cid, vs in sorted(groups.items()):
        vs.sort(key=lambda m: int(m.get("handoff_version", "H0")[1:]) if HV.fullmatch(str(m.get("handoff_version", ""))) else -1); m = vs[-1]; b = m.get("builder_assertions", {}); rt = m.get("runtime_binding", {})
        rows.append({"candidate_id": cid, "latest_handoff_version": m.get("handoff_version"), "baseline_sha": rt.get("baseline", {}).get("baseline_sha"), "head_sha": rt.get("candidate", {}).get("head_sha"), "requirement_ids": b.get("identity", {}).get("requirement_ids", []), "risk_tier": b.get("risk", {}).get("tier"), "dependencies": b.get("dependencies", {}).get("upstream", []), "collision_surfaces": b.get("dependencies", {}).get("collision_surfaces", []), "test_receipts": b.get("validation", {}).get("test_receipts", []), "review_status": b.get("review", {}).get("status"), "public_impact": b.get("public_consequence", {}).get("classification"), "limitations": b.get("limitations", {}), "builder_id": b.get("identity", {}).get("builder_id"), "state": m.get("computed", {}).get("state"), "history_versions": [x.get("handoff_version") for x in vs]})
    return {"schema_version": 1, "candidates": rows, "invalid": invalid}
def audit(paths: list[Path], dispositions: dict[str, Any] | None = None) -> dict[str, Any]:
    view = intake(paths); d = dispositions or {}; traces = []; findings = []
    for c in view["candidates"]:
        deferred = c.get("limitations", {}).get("deferred_portions", []); successors = [x.get("successor_work_item") for x in deferred if isinstance(x, dict)]; disp = d.get(c["candidate_id"])
        traces.append({"candidate_id": c["candidate_id"], "requirement_ids": c["requirement_ids"], "baseline_sha": c["baseline_sha"], "head_sha": c["head_sha"], "handoff_state": c["state"], "steward_disposition": disp, "successor": successors, "public_impact": c["public_impact"], "receipt_refs": [ref for r in c["test_receipts"] if isinstance(r, dict) for ref in r.get("artifact_refs", []) if isinstance(ref, str)]})
        if c["state"] in {"HANDOFF_BLOCKED", "STALE_HANDOFF"} and disp is None: findings.append({"candidate_id": c["candidate_id"], "code": "FAILED_OR_STALE_WITHOUT_STEWARD_DISPOSITION"})
        if deferred and not all(successors): findings.append({"candidate_id": c["candidate_id"], "code": "PARTIAL_IMPLEMENTATION_RESIDUE_WITHOUT_SUCCESSOR"})
        if c["public_impact"] == "PUBLIC_IMPACT_PRESENT" and disp == "MERGED_VERIFIED": findings.append({"candidate_id": c["candidate_id"], "code": "PUBLIC_HANDOFF_REQUIRES_SEPARATE_DISPOSITION"})
    return {"schema_version": 1, "traces": traces, "findings": findings, "invalid_handoffs": view["invalid"]}
def migration_classify(r: dict[str, Any]) -> str:
    if r.get("superseded") is True: return "SUPERSEDED"
    if r.get("head_stale") is True: return "STALE"
    if r.get("semantic_identity") and r.get("exact_head") and r.get("tests") and r.get("limitations"): return "STEWARD_REVIEW_REQUIRED" if r.get("steward_review_required") else "COMPLETE_HANDOFF"
    if r.get("semantic_identity") and r.get("exact_head"): return "RECONSTRUCTABLE"
    if any(r.get(k) for k in ("semantic_identity", "exact_head", "tests", "limitations")): return "PARTIAL_HANDOFF"
    return "STEWARD_REVIEW_REQUIRED"
def template(cid: str, bid: str, reqs: list[str]) -> dict[str, Any]:
    return {"schema_version": 1, "kind": "N0TE_BUILDER_HANDOFF_DECLARATION", "candidate_id": cid, "handoff_version": "H0", "builder_assertions": {"identity": {"builder_id": bid, "candidate_id": cid, "requirement_ids": reqs, "pub_ids": [], "product_outcome": "", "artist_job": "", "semantic_owner": ""}, "bounded_increment": {"increment_id": cid, "description": "", "cohesive": True, "unrelated_concerns": False, "partial_failure_blocks_unrelated": False, "mixed_risk_surfaces": False, "independent_disposition_possible": False, "external_dependency_partial": False, "review_opaque": False}, "semantic_contract": {"governing_requirement": reqs[0] if reqs else "", "required_behavior": "", "non_negotiable_semantics": [], "allowed_implementation_freedom": [], "interpretations_required": [], "remaining_semantics": []}, "behavior": {"requested_behavior": "", "implemented_behavior": "", "consumer_path": {"applicable": False, "reason": "", "path": None, "proof": None}, "apis": [], "contracts": [], "persistence": [], "schemas": [], "migrations": [], "background_jobs": [], "provider_behavior": [], "platform_implications": []}, "dependencies": {"upstream": [], "downstream_consumers": [], "shared_domains": [], "shared_files": [], "ordering_assumptions": [], "provider_assumptions": [], "platform_assumptions": [], "semantic_assumptions": [], "collision_surfaces": []}, "validation": {"test_receipts": [], "regression_proof": "", "consumer_smoke": "", "full_regression": "", "migration_proof": "", "recovery_proof": "", "security_privacy_rights_proof": "", "cross_platform_proof": ""}, "review": {"status": "NOT_REVIEWED", "unresolved_findings": [], "inline_threads": [], "reviewer_assumptions": []}, "risk": {"tier": "TIER_1", "high_consequence_surfaces": [], "failure_modes": [], "rollback_recovery_considerations": []}, "public_consequence": {"classification": "PUBLIC_IMPACT_NONE"}, "limitations": {"known_limitations": [], "deferred_portions": [], "external_dependencies": [], "provider_dependencies": [], "human_acceptance_needs": [], "public_acceptance_needs": []}, "steward_handoff": {"expected_disposition": "QUALIFY", "exact_head_to_review": "RUNTIME_EXACT", "candidate_frozen_for_qualification": True, "builder_available_for_fix": True, "dependency_safe_continuation": []}, "lenses": [], "lineage": {"previous_handoff_version": None, "order": None}}}

def cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(); s = p.add_subparsers(dest="cmd", required=True)
    q=s.add_parser("init"); q.add_argument("--candidate-id",required=True);q.add_argument("--builder-id",required=True);q.add_argument("--requirement",action="append",required=True);q.add_argument("--output",required=True)
    q=s.add_parser("seal");q.add_argument("--repo",default=".");q.add_argument("--declaration",required=True);q.add_argument("--baseline-sha",required=True);q.add_argument("--current-main-sha",required=True);q.add_argument("--baseline-branch",default="main");q.add_argument("--expected-head");q.add_argument("--pr");q.add_argument("--output")
    q=s.add_parser("validate");q.add_argument("--manifest",required=True);q.add_argument("--repo")
    q=s.add_parser("successor");q.add_argument("--previous",required=True);q.add_argument("--order-type",choices=sorted(ORDERS),required=True);q.add_argument("--order-id",required=True);q.add_argument("--output",required=True)
    q=s.add_parser("intake");q.add_argument("paths",nargs="+");q.add_argument("--output")
    q=s.add_parser("audit");q.add_argument("paths",nargs="+");q.add_argument("--dispositions");q.add_argument("--output")
    q=s.add_parser("migrate");q.add_argument("--input",required=True);q.add_argument("--output")
    a=p.parse_args(argv)
    try:
        if a.cmd=="init": write(Path(a.output),template(a.candidate_id,a.builder_id,a.requirement));print(a.output);return 0
        if a.cmd=="seal":
            m=seal(load(Path(a.declaration)),collect(Path(a.repo),a.baseline_sha,a.current_main_sha,a.baseline_branch,a.pr),a.expected_head)
            if a.output: write(Path(a.output),m)
            print(m["computed"]["state"]); [print("- "+x) for x in m["computed"]["findings"]]; return 0 if m["computed"]["state"] in {"READY_HANDOFF","SPLIT_RECOMMENDED"} else 2
        if a.cmd=="validate":
            m=load(Path(a.manifest)); fs=validate(m,git(Path(a.repo),"rev-parse","HEAD") if a.repo else None); print("READY_HANDOFF" if not fs else ("STALE_HANDOFF" if any("stale" in x for x in fs) else "HANDOFF_BLOCKED")); [print("- "+x) for x in fs]; return 0 if not fs else 2
        if a.cmd=="successor": write(Path(a.output),successor(load(Path(a.previous)),a.order_type,a.order_id));print(a.output);return 0
        if a.cmd=="intake": result=intake([Path(x) for x in a.paths])
        elif a.cmd=="audit": result=audit([Path(x) for x in a.paths],load(Path(a.dispositions)) if a.dispositions else None)
        else:
            records=json.loads(Path(a.input).read_text()); result=[{**r,"classification":migration_classify(r)} for r in records if isinstance(r,dict)]
        if a.output: write(Path(a.output),result)
        else: print(json.dumps(result,indent=2))
        return 0
    except BuilderHandoffError as exc: print(f"HANDOFF_BLOCKED\n- {exc}",file=sys.stderr);return 2
if __name__ == "__main__": raise SystemExit(cli())
