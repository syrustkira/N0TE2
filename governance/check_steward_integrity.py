#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, os, re, subprocess, sys
from pathlib import Path
from typing import Any

HEX40 = re.compile(r"^[0-9a-f]{40}$")
REQ_ID = re.compile(r"^REQ-SCOPE-(\d{3})$")
REQ_TOKEN = re.compile(r"^(?:REQ-SCOPE-)?(\d{3})(?:-(?:REQ-SCOPE-)?(\d{3}))?$")
ACCEPTANCE_STATES = ["MAPPED","IMPLEMENTED","INTEGRATED","REACHABLE","VERIFIED","RECOVERABLE","AUTHORITY_SAFE","CONSUMER_ACCEPTED","VALUE_EVIDENCED"]
ACCEPTANCE_LINKS = ["requirement_id","canonical_scope_ref","implementation_refs","integration_refs","user_reachability_refs","verification_refs","failure_recovery_refs","authority_security_refs","consumer_acceptance_refs","value_evidence_refs"]
DISCOVERY_DISPOSITIONS = {"EXECUTED","DURABLY_CAPTURED","DUPLICATE","BLOCKED","REJECTED","NON_ACTIONABLE"}
EQUIV_FIELDS = {"original_requirement","replacement_implementation","semantic_coverage_mapping","retained_behavior","changed_behavior","uncovered_residue","acceptance_evidence","semantic_authority","lineage","successor_status"}


class StewardIntegrityError(RuntimeError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise StewardIntegrityError(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise StewardIntegrityError(f"cannot load {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path} must contain an object")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if raw.strip():
                value = json.loads(raw)
                require(isinstance(value, dict), f"{path}:{lineno} is not an object")
                rows.append(value)
    except Exception as exc:
        raise StewardIntegrityError(f"cannot load {path}: {exc}") from exc
    return rows


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT).strip()


def canonical_requirement_id(number: int) -> str:
    return f"REQ-SCOPE-{number:03d}"


def expand_requirement_spec(value: Any) -> set[str]:
    if value in (None, ""):
        return set()
    if isinstance(value, list):
        out = set()
        for item in value:
            out |= expand_requirement_spec(item)
        return out
    require(isinstance(value, str), "requirement expression must be text or list")
    out = set()
    for raw in value.split(","):
        token = raw.strip()
        if not token:
            continue
        match = REQ_TOKEN.fullmatch(token)
        require(match is not None, f"invalid requirement token: {token}")
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        require(start <= end, f"descending requirement range: {token}")
        out |= {canonical_requirement_id(n) for n in range(start, end + 1)}
    return out


def normalize_requirement_ids(values: Any, field: str = "requirements") -> list[str]:
    if values is None:
        return []
    require(isinstance(values, list), f"{field} must be a list")
    require(all(isinstance(v, str) and REQ_ID.fullmatch(v.strip()) for v in values), f"{field} contains malformed ids")
    out = [v.strip() for v in values]
    require(len(out) == len(set(out)), f"{field} contains duplicates")
    return out


def graph_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = graph.get("nodes")
    require(isinstance(rows, list), "completion_graph.nodes must be a list")
    out = {}
    for row in rows:
        require(isinstance(row, dict), "completion graph node must be an object")
        node_id = row.get("id")
        require(isinstance(node_id, str) and node_id, "completion graph node lacks id")
        require(node_id not in out, f"duplicate completion graph node id: {node_id}")
        out[node_id] = row
    return out


def validate_contract(contract: dict[str, Any]) -> None:
    require(contract.get("schema_version") == 1, "integrity contract schema must be 1")
    require(contract.get("contract_id") == "STEWARD-CROSS-LEDGER-INTEGRITY-001", "unexpected integrity contract id")
    require(contract.get("role") == "VALIDATION_ONLY_NO_SEMANTIC_AUTHORITY", "integrity checker became semantic authority")
    boundary = contract.get("authority_boundary", {})
    require(boundary.get("creates_second_steward") is False, "integrity checer cannot create a second Steward")
    require(boundary.get("can_mutate_main") is False, "integrity checker cannot mutate main")
    require(boundary.get("can_accept_artist_value") is False, "integrity checker cannot fabricate artist acceptance")
    legacy = contract.get("legacy_compatibility", {})
    require(legacy.get("new_supersession_requires_evidence") is True, "new supersession must require evidence")
    require(legacy.get("historical_supersession_without_local_proof_is_not_equivalence") is True, "historical supersession cannot become implicit equivalence")


def validate_requirement_graph(requirements: dict[str, Any], graph: dict[str, Any]):
    canonical = requirements.get("canonical_scope", {})
    start, end, count = canonical.get("start"), canonical.get("end"), canonical.get("retained_requirement_count")
    require(type(start) is int and type(end) is int and start <= end, "canonical range invalid")
    require(count == end - start + 1, "canonical retained count does not match range")
    canonical_ids = {canonical_requirement_id(n) for n in range(start, end + 1)}
    nodes = graph_index(graph)
    graph_requirements = set()
    for node_id, row in nodes.items():
        graph_requirements |= expand_requirement_spec(row.get("requirements"))
        for field in ("depends_on", "any_of"):
            refs = row.get(field, []) or []
            require(isinstance(refs, list), f"{node_id}.{field} must be a list")
            for ref in refs:
                require(isinstance(ref, str) and ref in nodes, f"{node_id}.{field} references unknown node: {ref}")
    unknown = sorted(graph_requirements - canonical_ids)
    require(not unknown, "completion graph references requirements outside canonical scope: " + ", ".join(unknown))

    held = set(normalize_requirement_ids(requirements.get("held_or_boundary"), "held_or_boundary"))
    require(held <= canonical_ids, "held scope escaped canonical range")
    require("LATER-01" in nodes, "completion graph lost LATER-01")
    require(expand_requirement_spec(nodes["LATER-01"].get("requirements")) == held, "held_or_boundary and LATER-01 diverged; retained work may have been silently dropped or invented")

    superseded = set(normalize_requirement_ids(requirements.get("superseded"), "superseded"))
    require(superseded <= canonical_ids, "superseded scope escaped canonical range")
    require(not held & superseded, "requirement cannot be held and superseded")
    require(not graph_requirements & superseded, "superseded requirement still appears in construction graph")

    extensions = requirements.get("canonical_extensions")
    require(isinstance(extensions, list), "canonical_extensions must be a list")
    seen = set()
    for row in extensions:
        require(isinstance(row, dict), "canonical extension must be an object")
        req_id = row.get("id")
        require(isinstance(req_id, str) and REQ_ID.fullmatch(req_id), "malformed canonical extension id")
        require(req_id in canonical_ids and req_id not in seen, f"invalid/duplicate canonical extension: {req_id}")
        seen.add(req_id)
        require(row.get("state") == "MAPPED" and row.get("selected") is False, f"canonical extension {req_id} self-selected or changed state")
        affinities = row.get("construction_affinity")
        require(isinstance(affinities, list) and affinities, f"canonical extension {req_id} lacks affinity")
        require(all(a in nodes for a in affinities), f"canonical extension {req_id} references unknown affinity")

    sequence = requirements.get("sequence", {})
    require(type(sequence.get("start")) is int and type(sequence.get("end") is int, "build sequence invalid")
    require(start <= sequence["start"] <= sequence["end"] <= end, "build sequence escaped canonical range")
    return canonical_ids, superseded, nodes


def validate_decisions(rows: list[dict[str, Any]]) -> None:
    by_id, successors = {}, {}
    for row in rows:
        decision_id = row.get("id")
        require(isinstance(decision_id, str) and decision_id, "decision row lacks id")
        require(decision_id not in by_id, f"duplicate decision id: {decision_id}")
        bt—P–ÄL@≈9I 