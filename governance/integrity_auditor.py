from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

OPEN_FINDING_STATES = {"OPEN", "ROUTED", "ACKNOWLEDGED", "REMEDIATION_PENDING", "REVALIDATION_PENDING"}
TERMINAL_FINDING_STATES = {"RESOLVED", "SUPERSEDED", "FALSE_POSITIVE_WITH_PROOF"}
BLOCKING_SEVERITIES = {"INTEGRITY_BLOCKING", "INTEGRITY_CRITICAL"}
REQUIREMENT_ID = re.compile(r"^REQ-SCOPE-(\d{3})$")


class IntegrityError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_hash(value: Any, length: int = 24) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise IntegrityError(f"cannot load JSON {path}: {exc}") from exc


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IntegrityError(f"malformed JSONL {path}:{line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise IntegrityError(f"JSONL row must be object: {path}:{line_no}")
        rows.append(row)
    return rows


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def dump_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(dict(row), sort_keys=True, ensure_ascii=False) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def parse_requirement_spec(spec: str) -> set[str]:
    result: set[str] = set()
    for token in re.split(r"\s*,\s*", spec.strip()):
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            if left.isdigit() and right.isdigit():
                result.update(f"REQ-SCOPE-{n:03d}" for n in range(int(left), int(right) + 1))
                continue
        if token.isdigit():
            result.add(f"REQ-SCOPE-{int(token):03d}")
    return result


@dataclasses.dataclass(frozen=True)
class Node:
    id: str
    kind: str
    source: str
    attrs: dict[str, Any]

    @classmethod
    def from_dict(cls, row: Mapping[str, Any], default_source: str = "SNAPSHOT") -> "Node":
        node_id = str(row.get("id") or "").strip()
        kind = str(row.get("kind") or "").strip().upper()
        if not node_id or not kind:
            raise IntegrityError(f"node requires id and kind: {row!r}")
        return cls(node_id, kind, str(row.get("source") or default_source), dict(row.get("attrs") or {}))

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class Edge:
    src: str
    rel: str
    dst: str
    source: str
    attrs: dict[str, Any]

    @classmethod
    def from_dict(cls, row: Mapping[str, Any], default_source: str = "SNAPSHOT") -> "Edge":
        src = str(row.get("src") or "").strip()
        rel = str(row.get("rel") or "").strip().upper()
        dst = str(row.get("dst") or "").strip()
        if not src or not rel or not dst:
            raise IntegrityError(f"edge requires src, rel and dst: {row!r}")
        return cls(src, rel, dst, str(row.get("source") or default_source), dict(row.get("attrs") or {}))

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class IntegrityGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.outgoing: dict[str, list[Edge]] = defaultdict(list)
        self.incoming: dict[str, list[Edge]] = defaultdict(list)
        self.source_health: dict[str, dict[str, Any]] = {}
        self.raw_records: list[dict[str, Any]] = []

    def add_node(self, node: Node) -> None:
        prior = self.nodes.get(node.id)
        if prior and prior != node:
            raise IntegrityError(f"ambiguous node identity {node.id}: {prior.source} vs {node.source}")
        self.nodes[node.id] = node

    def add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)
        self.outgoing[edge.src].append(edge)
        self.incoming[edge.dst].append(edge)

    def kind(self, *kinds: str) -> list[Node]:
        wanted = {kind.upper() for kind in kinds}
        return [node for node in self.nodes.values() if node.kind in wanted]

    def cone(self, seeds: Iterable[str]) -> set[str]:
        seen: set[str] = set()
        queue: deque[str] = deque(str(seed) for seed in seeds)
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            for edge in self.outgoing.get(current, []):
                if edge.dst not in seen:
                    queue.append(edge.dst)
            for edge in self.incoming.get(current, []):
                if edge.src not in seen:
                    queue.append(edge.src)
        return seen

    def to_dict(self) -> dict[str, Any]:
        nodes = [n.to_dict() for n in sorted(self.nodes.values(), key=lambda n: n.id)]
        edges = [e.to_dict() for e in sorted(self.edges, key=lambda e: (e.src, e.rel, e.dst))]
        return {"nodes": nodes, "edges": edges, "source_health": self.source_health, "raw_records": self.raw_records, "graph_hash": stable_hash({"nodes": nodes, "edges": edges}, 40)}

    @classmethod
    def from_snapshot(cls, payload: Mapping[str, Any]) -> "IntegrityGraph":
        graph = cls()
        for row in payload.get("nodes", []) or []:
            graph.add_node(Node.from_dict(row))
        for row in payload.get("edges", []) or []:
            graph.add_edge(Edge.from_dict(row))
        graph.source_health.update(dict(payload.get("source_health") or {}))
        graph.raw_records.extend(list(payload.get("raw_records") or []))
        return graph


@dataclasses.dataclass(frozen=True)
class Invariant:
    id: str
    purpose: str
    severity: str
    routing_authority: str
    blocking: bool
    sources: list[str]
    test: str


@dataclasses.dataclass
class Finding:
    finding_id: str
    invariant_id: str
    severity: str
    detected_at: str
    audit_run_id: str
    affected_object_ids: list[str]
    authoritative_sources_consulted: list[str]
    conflicting_or_missing_edges: list[str]
    exact_evidence: dict[str, Any]
    current_consequence: str
    blocked_cone: list[str]
    remediation_authority: str
    recommended_disposition: str
    related_receipt_ids: list[str]
    trace_ids: list[str]
    freshness: dict[str, Any]
    state: str = "OPEN"
    resolution_evidence: dict[str, Any] | None = None
    resolved_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def load_invariants(path: Path) -> dict[str, Invariant]:
    doc = load_json(path)
    rows = doc.get("invariants") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise IntegrityError("integrity invariant catalog requires an invariants array")
    result: dict[str, Invariant] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            raise IntegrityError("malformed integrity invariant")
        inv = Invariant(
            id=str(row["id"]),
            purpose=str(row.get("purpose") or ""),
            severity=str(row.get("severity") or "INTEGRITY_WARNING"),
            routing_authority=str(row.get("routing_authority") or "N0TE-SUPERVISOR"),
            blocking=bool(row.get("blocking", False)),
            sources=[str(v) for v in row.get("sources", [])],
            test=str(row.get("test") or row["id"]),
        )
        result[inv.id] = inv
    return result


class RepositoryAdapter:
    REQUIRED_LOCAL = (
        "governance/requirements.json",
        "governance/completion_graph.json",
        "governance/current_state.json",
        "governance/active_receipt.json",
        "governance/automation_registry.json",
        "governance/authority.json",
        "governance/invariants.json",
    )
    REQUIRED_EXTERNAL = (
        "TELLMEN0TE_PUBLIC_RUNTIME",
        "PROVIDER_EVIDENCE",
        "RIGHTS_PROVENANCE_EVIDENCE",
        "HUMAN_ACCEPTANCE_EVIDENCE",
    )

    def __init__(self, repo: Path):
        self.repo = repo

    def build(self) -> IntegrityGraph:
        graph = IntegrityGraph()
        missing = [path for path in self.REQUIRED_LOCAL if not (self.repo / path).is_file() or (self.repo / path).is_symlink()]
        for path in self.REQUIRED_LOCAL:
            graph.source_health[path] = {"available": path not in missing, "required": True}
        if missing:
            raise IntegrityError("missing required repository sources: " + ", ".join(missing))
        requirements = load_json(self.repo / "governance/requirements.json")
        completion = load_json(self.repo / "governance/completion_graph.json")
        current = load_json(self.repo / "governance/current_state.json")
        receipt = load_json(self.repo / "governance/active_receipt.json")
        registry = load_json(self.repo / "governance/automation_registry.json")

        canonical = requirements.get("canonical_scope") or {}
        held = set(requirements.get("held_or_boundary") or [])
        superseded = set(requirements.get("superseded") or [])
        extensions = {row.get("id"): row for row in requirements.get("canonical_extensions", []) if isinstance(row, dict) and row.get("id")}
        for number in range(int(canonical.get("start", 0)), int(canonical.get("end", -1)) + 1):
            req_id = f"REQ-SCOPE-{number:03d}"
            extension = extensions.get(req_id)
            disposition = "SUPERSEDED_DECLARED" if req_id in superseded else "HELD" if req_id in held else "MAPPED_UNSELECTED" if extension and str(extension.get("state") or "").upper() == "MAPPED" and extension.get("selected") is False else "RETAINED"
            attrs = {"accepted": True, "disposition": disposition, "source_revision": canonical.get("source_revision")}
            if extension:
                attrs["extension"] = extension
            graph.add_node(Node(req_id, "REQUIREMENT", "governance/requirements.json", attrs))

        for row in completion.get("nodes", []) or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            candidate_id = f"CONSTRUCTION:{row['id']}"
            graph.add_node(Node(candidate_id, "CONSTRUCTION_STATE", "governance/completion_graph.json", {"status": row.get("state"), "construction_id": row.get("id"), "required": row.get("required")}))
            for req_id in parse_requirement_spec(str(row.get("requirements") or "")):
                if req_id in graph.nodes:
                    graph.add_edge(Edge(candidate_id, "SERVES", req_id, "governance/completion_graph.json", {}))
            for dependency in row.get("depends_on", []) or []:
                graph.add_edge(Edge(candidate_id, "DEPENDS_ON", f"CONSTRUCTION:{dependency}", "governance/completion_graph.json", {}))

        graph.add_node(Node("MAIN:CURRENT", "MAIN_STATE", "governance/current_state.json", {
            "status": current.get("lifecycle_state"),
            "active_node": current.get("active_node"),
            "active_increment": current.get("active_increment"),
            "product_code_authorized": current.get("product_code_authorized"),
            "repository_sha": os.environ.get("N0TE2_HEAD_SHA"),
        }))
        receipt_id = str(receipt.get("receipt_id") or receipt.get("id") or "ACTIVE_RECEIPT")
        graph.add_node(Node(f"RECEIPT:{receipt_id}", "IMPLEMENTATION_RECEIPT", "governance/active_receipt.json", dict(receipt)))

        for actor in registry.get("actors", []) or []:
            if not isinstance(actor, dict) or not actor.get("id"):
                continue
            lifecycle = actor.get("lifecycle") if isinstance(actor.get("lifecycle"), dict) else {}
            graph.add_node(Node(f"ACTOR:{actor['id']}", "AUTONOMOUS_ACTOR", "governance/automation_registry.json", {
                "role_class": actor.get("role_class"), "authority": actor.get("authority"),
                "allowed_mutations": list(actor.get("allowed_mutations") or []), "parent": actor.get("parent"),
                "reports_to": actor.get("reports_to"), "state": lifecycle.get("state"), "kind": actor.get("kind"),
            }))

        for optional in ("governance/incidents.jsonl", "governance/provenance.jsonl", "governance/decisions.jsonl", "governance/definitions.jsonl", "governance/trajectory_audits.jsonl"):
            path = self.repo / optional
            graph.source_health[optional] = {"available": path.exists(), "required": False}
            if not path.exists():
                continue
            for row in load_jsonl(path):
                if optional.endswith("incidents.jsonl"):
                    incident_id = str(row.get("id") or stable_hash(row))
                    graph.add_node(Node(f"INCIDENT:{incident_id}", "INCIDENT", optional, row))
                else:
                    graph.raw_records.append({"source": optional, **row})

        for source in self.REQUIRED_EXTERNAL:
            graph.source_health[source] = {"available": False, "required": True, "reason": "external snapshot not supplied"}
        return graph


def merge_external_snapshot(graph: IntegrityGraph, payload: Mapping[str, Any], source_name: str = "EXTERNAL_SNAPSHOT") -> None:
    for row in payload.get("nodes", []) or []:
        graph.add_node(Node.from_dict(row, source_name))
    for row in payload.get("edges", []) or []:
        graph.add_edge(Edge.from_dict(row, source_name))
    for key, value in (payload.get("source_health") or {}).items():
        graph.source_health[str(key)] = dict(value) if isinstance(value, dict) else {"available": bool(value)}
    graph.raw_records.extend(list(payload.get("raw_records") or []))


def event_paths_to_seeds(graph: IntegrityGraph, paths: Sequence[str]) -> list[str]:
    seeds: set[str] = set()
    unknown = False
    for raw in paths:
        path = str(raw).strip().replace("\\", "/")
        if not path:
            continue
        if path == "governance/requirements.json":
            seeds.update(node.id for node in graph.kind("REQUIREMENT", "PUB_REQUIREMENT"))
        elif path == "governance/completion_graph.json":
            seeds.update(node.id for node in graph.kind("CONSTRUCTION_STATE", "CANDIDATE"))
        elif path in {"governance/current_state.json", "governance/active_receipt.json"}:
            seeds.add("MAIN:CURRENT")
            seeds.update(node.id for node in graph.kind("IMPLEMENTATION_RECEIPT"))
        elif path == "governance/automation_registry.json":
            seeds.update(node.id for node in graph.kind("AUTONOMOUS_ACTOR"))
        elif path == "governance/incidents.jsonl":
            seeds.update(node.id for node in graph.kind("INCIDENT"))
        elif path.startswith("integrity-runtime/") or path.startswith(".integrity-prior/"):
            continue
        else:
            unknown = True
    return [] if unknown else sorted(seeds)


class Auditor:
    def __init__(self, graph: IntegrityGraph, invariants: Mapping[str, Invariant], run_id: str, prior_graph: IntegrityGraph | None = None, event_seeds: Sequence[str] | None = None):
        self.graph = graph
        self.invariants = invariants
        self.run_id = run_id
        self.prior_graph = prior_graph
        self.findings: list[Finding] = []
        self.skipped: list[dict[str, str]] = []
        self.errors: list[str] = []
        self.consulted_sources = sorted(key for key, value in graph.source_health.items() if value.get("available"))
        self.allowed_objects: set[str] | None = None
        if event_seeds:
            direct = {seed for seed in event_seeds if seed in graph.nodes}
            if direct:
                self.allowed_objects = graph.cone(direct)

    def _spec(self, invariant_id: str) -> Invariant:
        try:
            return self.invariants[invariant_id]
        except KeyError as exc:
            raise IntegrityError(f"invariant not cataloged: {invariant_id}") from exc

    def _has_edge(self, node_id: str, rels: set[str]) -> bool:
        return any(edge.rel in rels for edge in self.graph.outgoing.get(node_id, [])) or any(edge.rel in rels for edge in self.graph.incoming.get(node_id, []))

    def add(self, invariant_id: str, object_ids: Sequence[str], evidence: Mapping[str, Any], missing_edges: Sequence[str] = (), consequence: str = "Integrity reconciliation required.", disposition: str = "VERIFY_REQUIRED", *, severity: str | None = None, blocking: bool | None = None) -> None:
        object_ids = sorted({str(value) for value in object_ids if value})
        if self.allowed_objects is not None and not (set(object_ids) & self.allowed_objects):
            return
        spec = self._spec(invariant_id)
        actual_blocking = spec.blocking if blocking is None else blocking
        finding_key = {"invariant": invariant_id, "objects": object_ids, "identity": evidence.get("identity") or evidence.get("version") or evidence.get("missing") or evidence.get("status_pair") or ""}
        self.findings.append(Finding(
            finding_id=f"FND-{stable_hash(finding_key, 20).upper()}", invariant_id=invariant_id,
            severity=severity or spec.severity, detected_at=utc_now(), audit_run_id=self.run_id,
            affected_object_ids=object_ids, authoritative_sources_consulted=self.consulted_sources,
            conflicting_or_missing_edges=list(missing_edges), exact_evidence=dict(evidence),
            current_consequence=consequence, blocked_cone=sorted(self.graph.cone(object_ids)) if actual_blocking else [],
            remediation_authority=spec.routing_authority, recommended_disposition=disposition,
            related_receipt_ids=[], trace_ids=[], freshness={},
        ))

    def run(self) -> list[Finding]:
        for check in (self.orphan_requirements, self.disappeared_requirements, self.receipt_chains, self.public_handoffs, self.rights_integrity, self.supersession_integrity, self.status_contradictions, self.authority_collisions, self.staleness, self.acceptance_integrity):
            try:
                check()
            except Exception as exc:
                self.errors.append(f"{check.__name__}: {exc}")
        return self.findings

    def orphan_requirements(self) -> None:
        valid_rels = {"SERVES", "IMPLEMENTS", "BLOCKED_BY", "SUPERSEDES", "REPLACES", "REMOVED_BY", "VERIFIED_BY"}
        terminal_dispositions = {"HELD", "WAITING", "BLOCKED", "DEFERRED", "MAPPED_UNSELECTED", "REMOVED_AUTHORIZED", "COMPLETED", "SUPERSEDED_WITH_PROOF"}
        for req in self.graph.kind("REQUIREMENT", "PUB_REQUIREMENT"):
            if not req.attrs.get("accepted", False) or str(req.attrs.get("disposition") or "").upper() in terminal_dispositions:
                continue
            edges = [edge for edge in self.graph.incoming.get(req.id, []) + self.graph.outgoing.get(req.id, []) if edge.rel in valid_rels]
            candidates: list[Node] = []
            for edge in edges:
                other = self.graph.nodes.get(edge.src if edge.dst == req.id else edge.dst)
                if other and other.kind == "CANDIDATE":
                    candidates.append(other)
            if candidates and all(str(candidate.attrs.get("status") or "").upper() in {"STALE", "ABANDONED", "FAILED_STALE"} for candidate in candidates):
                self.add("ORPHAN_REQUIREMENT_AFTER_BRANCH_STALE", [req.id] + [c.id for c in candidates], {"missing": "live_successor_path"}, ["FIXED_BY", "REBUILT_BY", "SERVES"], "Accepted scope survives but its only implementation path is stale.", "ROUTE_TO_STEWARD", severity="INTEGRITY_HIGH", blocking=False)
            elif not edges:
                inv = "ORPHAN_PUBLIC_REQUIREMENT" if req.kind == "PUB_REQUIREMENT" else "ORPHAN_REQUIREMENT_NO_STATE"
                self.add(inv, [req.id], {"missing": "current_path", "disposition": req.attrs.get("disposition")}, ["CURRENT_DISPOSITION"], "Accepted scope has no durable current path.", "ROUTE_TO_SEMANTIC_AUTHORITY")

    def disappeared_requirements(self) -> None:
        if self.prior_graph is None:
            self.skipped.append({"invariant": "DISAPPEARED_REQUIREMENT", "reason": "no prior authoritative graph snapshot"})
            return
        current = {node.id for node in self.graph.kind("REQUIREMENT", "PUB_REQUIREMENT")}
        for prior in self.prior_graph.kind("REQUIREMENT", "PUB_REQUIREMENT"):
            if not prior.attrs.get("accepted", False) or prior.id in current:
                continue
            explained = any(node.attrs.get("subject_id") == prior.id and node.kind in {"SCOPE_CHANGE", "TOMBSTONE", "SUPERSESSION", "EQUIVALENCE_RECEIPT"} for node in self.graph.nodes.values())
            if not explained:
                self.add("DISAPPEARED_REQUIREMENT", [prior.id], {"missing": "authorized_disappearance_lineage"}, ["REMOVED_BY", "SUPERSEDES", "EQUIVALENT_TO"], "Previously accepted scope disappeared without lineage.", "ROUTE_TO_SEMANTIC_AUTHORITY")

    def receipt_chains(self) -> None:
        receipts = self.graph.kind("MERGE_RECEIPT", "IMPLEMENTATION_RECEIPT", "COMPLETION_RECEIPT", "PUBLIC_RECEIPT", "EQUIVALENCE_RECEIPT")
        for receipt in receipts:
            subject = receipt.attrs.get("subject_id")
            if subject and subject not in self.graph.nodes:
                self.add("DANGLING_RECEIPT", [receipt.id, str(subject)], {"missing": "receipt_subject"}, ["TRACES_TO"], "Receipt points to an unresolved subject.", "ROUTE_TO_STEWARD", severity="INTEGRITY_HIGH", blocking=False)
        for candidate in self.graph.kind("CANDIDATE"):
            status = str(candidate.attrs.get("status") or "").upper()
            if status not in {"MERGED", "MERGED_VERIFIED", "DONE"}:
                continue
            merge_receipts = [node for node in self.graph.kind("MERGE_RECEIPT") if node.attrs.get("subject_id") == candidate.id or self._has_direct_link(node.id, candidate.id)]
            if not merge_receipts and candidate.attrs.get("requires_merge_receipt", True):
                self.add("MERGED_WITHOUT_MERGE_RECEIPT", [candidate.id], {"missing": "merge_receipt"}, ["MERGED_AS"], "Merged candidate lacks candidate-specific durable merge proof.", "ROUTE_TO_STEWARD")
            for receipt in merge_receipts:
                candidate_head = candidate.attrs.get("head_sha")
                receipt_head = receipt.attrs.get("head_sha")
                if candidate_head and receipt_head and candidate_head != receipt_head:
                    self.add("MERGE_RECEIPT_HEAD_MISMATCH", [candidate.id, receipt.id], {"identity": candidate.id, "candidate_head": candidate_head, "receipt_head": receipt_head}, [], "Merge receipt is bound to the wrong candidate head.", "ROUTE_TO_STEWARD")
                expected = candidate.attrs.get("predecessor_sha")
                actual = receipt.attrs.get("predecessor_sha")
                if expected and actual and expected != actual:
                    self.add("MERGE_RECEIPT_PREDECESSOR_MISMATCH", [candidate.id, receipt.id], {"identity": candidate.id, "expected": expected, "actual": actual}, [], "Merge receipt predecessor contradicts repository history.", "ROUTE_TO_STEWARD")
        for completion in self.graph.kind("COMPLETION_RECEIPT"):
            if not completion.attrs.get("implementation_evidence"):
                self.add("COMPLETION_WITHOUT_IMPLEMENTATION", [completion.id], {"missing": "implementation_evidence"}, ["IMPLEMENTS"], "Completion claim lacks implementation evidence.", "ROUTE_TO_STEWARD")
            if completion.attrs.get("acceptance_required") and not completion.attrs.get("acceptance_evidence"):
                self.add("COMPLETION_WITHOUT_REQUIRED_ACCEPTANCE", [completion.id], {"missing": "acceptance_evidence"}, ["ACCEPTED_BY"], "Completion class requires acceptance proof that is absent.", "ROUTE_TO_ACCEPTANCE_AUTHORITY")
        groups: dict[tuple[str, str], list[Node]] = defaultdict(list)
        for receipt in receipts:
            subject = str(receipt.attrs.get("subject_id") or "")
            version = str(receipt.attrs.get("object_version") or receipt.attrs.get("version") or "")
            if subject:
                groups[(subject, version)].append(receipt)
        for key, values in groups.items():
            statuses = {str(value.attrs.get("status") or "") for value in values}
            if len(statuses - {""}) > 1:
                self.add("DUPLICATE_CONTRADICTORY_RECEIPTS", [value.id for value in values], {"identity": key, "statuses": sorted(statuses)}, [], "Receipts disagree for the same object/version.", "ROUTE_TO_STEWARD", severity="INTEGRITY_HIGH", blocking=False)

    def _has_direct_link(self, left: str, right: str) -> bool:
        return any(edge.dst == right for edge in self.graph.outgoing.get(left, [])) or any(edge.src == right for edge in self.graph.incoming.get(left, []))

    def public_handoffs(self) -> None:
        for candidate in self.graph.kind("CANDIDATE", "COMMIT", "IMPLEMENTATION_RECEIPT"):
            if candidate.attrs.get("public_consequence") and str(candidate.attrs.get("status") or "").upper() in {"MERGED", "MERGED_VERIFIED", "DONE", "COMPLETE"}:
                if not self._has_edge(candidate.id, {"HANDED_OFF_AS", "TRACES_TO"}):
                    self.add("MERGED_PUBLIC_CHANGE_WITHOUT_HANDOFF", [candidate.id], {"missing": "public_handoff"}, ["HANDED_OFF_AS"], "Merged public-consequence change lacks a public handoff.", "ROUTE_TO_PUBLIC_DEPLOYMENT_GOVERNANCE")
        for handoff in self.graph.kind("PUBLIC_HANDOFF"):
            upstream = self.graph.incoming.get(handoff.id, []) + self.graph.outgoing.get(handoff.id, [])
            has_engineering = any((self.graph.nodes.get(edge.src) and self.graph.nodes[edge.src].kind in {"CANDIDATE", "COMMIT", "IMPLEMENTATION_RECEIPT", "MERGE_RECEIPT"}) or (self.graph.nodes.get(edge.dst) and self.graph.nodes[edge.dst].kind in {"CANDIDATE", "COMMIT", "IMPLEMENTATION_RECEIPT", "MERGE_RECEIPT"}) for edge in upstream)
            if not has_engineering:
                self.add("ORPHAN_PUBLIC_HANDOFF", [handoff.id], {"missing": "implementation_lineage"}, ["DERIVED_FROM"], "Public handoff cannot resolve its engineering source.", "ROUTE_TO_PUBLIC_DEPLOYMENT_GOVERNANCE")
            age = handoff.attrs.get("age_days")
            waiting = str(handoff.attrs.get("status") or "").upper() in {"WAITING", "BLOCKED", "DEFERRED"}
            if isinstance(age, (int, float)) and age >= float(handoff.attrs.get("consume_after_days", 7)) and not waiting and not self._has_edge(handoff.id, {"DEPLOYED_AS", "PRODUCED_BY", "VERIFIED_BY"}):
                self.add("HANDOFF_NEVER_CONSUMED", [handoff.id], {"missing": "consumption_or_wait_state", "age_days": age}, ["DEPLOYED_AS"], "Handoff remains unconsumed without explicit wait/block state.", "ROUTE_TO_PUBLIC_DEPLOYMENT_GOVERNANCE", severity="INTEGRITY_WARNING", blocking=False)
        for deployment in self.graph.kind("PUBLIC_DEPLOYMENT", "PROVIDER_ACTION"):
            if deployment.attrs.get("mutation") and not self._has_edge(deployment.id, {"DEPLOYED_AS", "DERIVED_FROM", "TRACES_TO"}):
                self.add("PUBLIC_EXECUTION_WITHOUT_HANDOFF", [deployment.id], {"missing": "upstream_handoff"}, ["DERIVED_FROM"], "Public/provider mutation occurred without governed handoff.", "ROUTE_TO_PUBLIC_DEPLOYMENT_GOVERNANCE")
        for public in self.graph.kind("PUBLIC_DEPLOYMENT", "PUBLIC_RECEIPT"):
            if str(public.attrs.get("status") or "").upper() in {"PASS", "PUBLIC_VERIFIED", "COMPLETE"} and not (self._has_edge(public.id, {"OBSERVED_AS", "VERIFIED_BY"}) or public.attrs.get("observation_evidence")):
                self.add("PUBLIC_PASS_WITHOUT_OBSERVATION", [public.id], {"missing": "observation", "status": public.attrs.get("status")}, ["OBSERVED_AS"], "Public PASS lacks outside-world observation.", "ROUTE_TO_PUBLIC_DEPLOYMENT_GOVERNANCE")
        for failure in self.graph.kind("PUBLIC_FAILURE"):
            if failure.attrs.get("implicates_engineering") and not self._has_edge(failure.id, {"FIXED_BY", "REBUILT_BY", "BLOCKED_BY"}):
                self.add("PUBLIC_FAILURE_WITHOUT_ENGINEERING_DISPOSITION", [failure.id], {"missing": "steward_disposition"}, ["FIXED_BY", "REBUILT_BY"], "Public failure implicates engineering without Steward disposition.", "ROUTE_TO_STEWARD", severity="INTEGRITY_HIGH", blocking=False)

    def rights_integrity(self) -> None:
        for obj in list(self.graph.nodes.values()):
            if not obj.attrs.get("rights_sensitive"):
                continue
            evidence_nodes: list[Node] = []
            for edge in self.graph.outgoing.get(obj.id, []) + self.graph.incoming.get(obj.id, []):
                if edge.rel != "RIGHTS_PROVEN_BY":
                    continue
                other = self.graph.nodes.get(edge.dst if edge.src == obj.id else edge.src)
                if other and other.kind == "RIGHTS_EVIDENCE":
                    evidence_nodes.append(other)
            if not evidence_nodes:
                self.add("RIGHTS_REQUIRED_EVIDENCE_MISSING", [obj.id], {"missing": "rights_evidence", "version": obj.attrs.get("version")}, ["RIGHTS_PROVEN_BY"], "Rights-sensitive object lacks applicable evidence.", "ROUTE_TO_RIGHTS_AUTHORITY")
            for evidence in evidence_nodes:
                if obj.attrs.get("version") is not None and evidence.attrs.get("object_version") is not None and obj.attrs.get("version") != evidence.attrs.get("object_version"):
                    self.add("RIGHTS_EVIDENCE_VERSION_MISMATCH", [obj.id, evidence.id], {"version": obj.attrs.get("version"), "evidence_version": evidence.attrs.get("object_version")}, [], "Rights proof applies to another object version.", "ROUTE_TO_RIGHTS_AUTHORITY")
                if evidence.attrs.get("expired") is True:
                    self.add("RIGHTS_EVIDENCE_EXPIRED", [obj.id, evidence.id], {"identity": evidence.id, "expires_at": evidence.attrs.get("expires_at")}, [], "Rights evidence is expired.", "ROUTE_TO_RIGHTS_AUTHORITY")
                for axis in ("use", "platform", "territory", "derivative", "campaign", "commercial_context"):
                    required, allowed = obj.attrs.get(axis), evidence.attrs.get(axis)
                    if required is not None and allowed is not None and required != allowed and (not isinstance(allowed, list) or required not in allowed):
                        self.add("RIGHTS_EVIDENCE_SCOPE_MISMATCH", [obj.id, evidence.id], {"identity": f"{obj.id}:{axis}", "required": required, "allowed": allowed}, [], "Rights evidence scope does not cover actual use.", "ROUTE_TO_RIGHTS_AUTHORITY")
            if obj.attrs.get("derivative") and not self._has_edge(obj.id, {"DERIVED_FROM"}):
                self.add("DERIVATIVE_WITHOUT_PROVEN_LINEAGE", [obj.id], {"missing": "source_lineage"}, ["DERIVED_FROM"], "Derivative object cannot trace to source.", "ROUTE_TO_RIGHTS_AUTHORITY", severity="INTEGRITY_HIGH", blocking=False)
            if obj.attrs.get("published_before_rights_gate"):
                self.add("PUBLICATION_BEFORE_RIGHTS_GATE", [obj.id], {"identity": obj.id}, [], "Object was published before its rights gate resolved.", "ROUTE_TO_PUBLIC_DEPLOYMENT_GOVERNANCE")
            if str(obj.attrs.get("rights_status") or "").upper() in {"PASS", "CLEARED"} and not evidence_nodes:
                self.add("RIGHTS_PASS_WITHOUT_EVIDENCE", [obj.id], {"missing": "rights_evidence"}, ["RIGHTS_PROVEN_BY"], "Rights-cleared status is assertion-only.", "ROUTE_TO_RIGHTS_AUTHORITY")
        credits: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for node in self.graph.kind("RIGHTS_OBJECT", "RIGHTS_EVIDENCE", "ASSET", "ASSET_VERSION"):
            subject = node.attrs.get("subject_id") or node.attrs.get("asset_id")
            owner = node.attrs.get("owner") or node.attrs.get("author") or node.attrs.get("creator")
            role = node.attrs.get("representation_type") or "OWNER"
            if subject and owner:
                credits[(str(subject), str(role))][str(owner)].append(node.id)
        for key, owners in credits.items():
            if len(owners) > 1:
                ids = [item for values in owners.values() for item in values]
                self.add("CREDITS_RIGHTS_CONTRADICTION", ids, {"identity": key, "representations": sorted(owners)}, [], "Rights/credit representations conflict.", "ROUTE_TO_RIGHTS_AUTHORITY", severity="INTEGRITY_HIGH", blocking=False)

    def supersession_integrity(self) -> None:
        for req in self.graph.kind("REQUIREMENT", "PUB_REQUIREMENT"):
            if str(req.attrs.get("disposition") or "").upper() == "SUPERSEDED_DECLARED" and not self._has_edge(req.id, {"SUPERSEDES", "EQUIVALENT_TO", "REPLACES"}):
                self.add("SUPERSESSION_WITHOUT_EQUIVALENCE_RECEIPT", [req.id], {"missing": "equivalence_proof"}, ["EQUIVALENT_TO"], "Superseded scope lacks durable equivalence proof.", "ROUTE_TO_SEMANTIC_AUTHORITY", severity="INTEGRITY_HIGH", blocking=False)
        supersessions = self.graph.kind("SUPERSESSION")
        arcs: dict[str, set[str]] = defaultdict(set)
        for sup in supersessions:
            old_id, new_id = str(sup.attrs.get("old_id") or ""), str(sup.attrs.get("new_id") or "")
            if old_id and new_id:
                arcs[old_id].add(new_id)
            if new_id and new_id not in self.graph.nodes:
                self.add("DANGLING_SUPERSESSION", [sup.id, new_id], {"missing": "replacement_object"}, ["REPLACES"], "Supersession replacement does not exist.", "ROUTE_TO_SEMANTIC_AUTHORITY", severity="INTEGRITY_HIGH", blocking=False)
            proof = sup.attrs.get("equivalence_receipt_id")
            if str(sup.attrs.get("status") or "").upper() == "SUPERSEDED_BY_EQUIVALENT" and (not proof or proof not in self.graph.nodes):
                self.add("SUPERSESSION_WITHOUT_EQUIVALENCE_RECEIPT", [sup.id] + ([old_id] if old_id else []), {"missing": "equivalence_receipt"}, ["EQUIVALENT_TO"], "Equivalent supersession lacks durable proof.", "ROUTE_TO_SEMANTIC_AUTHORITY", severity="INTEGRITY_HIGH", blocking=False)
            if sup.attrs.get("partial") and not sup.attrs.get("residue_disposition"):
                self.add("PARTIAL_EQUIVALENCE_WITH_DROPPED_RESIDUE", [sup.id] + ([old_id] if old_id else []), {"missing": "residue_disposition"}, ["REPLACES"], "Partial supersession drops uncovered residue.", "ROUTE_TO_SEMANTIC_AUTHORITY", severity="INTEGRITY_HIGH", blocking=False)
            if sup.attrs.get("semantic_authority") is False:
                self.add("SEMANTICALLY_UNAUTHORIZED_SUPERSESSION", [sup.id], {"identity": sup.id}, [], "Implementation machinery changed semantic scope without authority.", "ROUTE_TO_SEMANTIC_AUTHORITY")
            if sup.attrs.get("acceptance_required") and not sup.attrs.get("acceptance_evidence"):
                self.add("SUPERSESSION_WITHOUT_ACCEPTANCE_EVIDENCE", [sup.id], {"missing": "acceptance_evidence"}, ["ACCEPTED_BY"], "Replacement lacks required acceptance proof.", "ROUTE_TO_ACCEPTANCE_AUTHORITY", severity="INTEGRITY_HIGH", blocking=False)
            if sup.attrs.get("equivalence_head_stale"):
                self.add("EQUIVALENCE_RECEIPT_HEAD_STALE", [sup.id], {"identity": sup.id}, [], "Equivalence proof is bound to stale implementation truth.", "ROUTE_TO_SEMANTIC_AUTHORITY", severity="INTEGRITY_WARNING", blocking=False)
            if sup.attrs.get("tombstone_without_authority"):
                self.add("TOMBSTONE_WITHOUT_AUTHORITY", [sup.id], {"identity": sup.id}, [], "Accepted semantic object was tombstoned without authority.", "ROUTE_TO_SEMANTIC_AUTHORITY")
        visited: set[str] = set()
        active: set[str] = set()

        def walk(node_id: str, path: list[str]) -> None:
            if node_id in active:
                cycle = path[path.index(node_id):] + [node_id] if node_id in path else path + [node_id]
                self.add("SUPERSESSION_CYCLE", cycle, {"identity": "->".join(cycle)}, [], "Supersession lineage contains a cycle.", "ROUTE_TO_SEMANTIC_AUTHORITY")
                return
            if node_id in visited:
                return
            active.add(node_id)
            for child in arcs.get(node_id, set()):
                walk(child, path + [node_id])
            active.remove(node_id)
            visited.add(node_id)

        for node_id in list(arcs):
            walk(node_id, [])

    def status_contradictions(self) -> None:
        for node in self.graph.nodes.values():
            status = str(node.attrs.get("status") or "").upper()
            if node.kind == "CANDIDATE" and status == "MERGED_VERIFIED" and node.attrs.get("present_on_main") is False:
                self.add("CANONICAL_STATUS_CONTRADICTION", [node.id], {"status_pair": "MERGED_VERIFIED/not-on-main"}, [], "Candidate merge status contradicts repository presence.", "ROUTE_TO_STEWARD")
            if node.kind in {"PUBLIC_DEPLOYMENT", "PUBLIC_RECEIPT"} and status == "PASS" and str(node.attrs.get("canonical_status") or "").upper() in {"VERIFY", "FAILED"}:
                self.add("CANONICAL_STATUS_CONTRADICTION", [node.id], {"status_pair": f"PASS/{node.attrs.get('canonical_status')}"}, [], "Public status contradicts canonical state.", "ROUTE_TO_PUBLIC_DEPLOYMENT_GOVERNANCE")
            if str(node.attrs.get("rights_status") or "").upper() == "CLEARED" and node.attrs.get("rights_evidence_missing"):
                self.add("CANONICAL_STATUS_CONTRADICTION", [node.id], {"status_pair": "RIGHTS_CLEARED/evidence-missing"}, [], "Rights status contradicts evidence state.", "ROUTE_TO_RIGHTS_AUTHORITY")

    def authority_collisions(self) -> None:
        actors = self.graph.kind("AUTONOMOUS_ACTOR")
        active_main: list[Node] = []
        for actor in actors:
            state = str(actor.attrs.get("state") or "").upper()
            role = str(actor.attrs.get("role_class") or "").upper()
            authority = str(actor.attrs.get("authority") or "").upper()
            mutations = {str(value).upper() for value in actor.attrs.get("allowed_mutations", [])}
            if state == "ACTIVE" and (role == "MAIN_WRITER" or authority == "MAIN_WRITER" or "MAIN" in mutations):
                active_main.append(actor)
            if "AUDITOR" in role and mutations & {"MAIN", "SEMANTIC_SCOPE", "PRODUCT_SCOPE", "PUBLIC_CANON", "PROVIDER_ACCOUNT", "RIGHTS_DECLARATION", "HUMAN_ACCEPTANCE"}:
                self.add("AUTHORITY_COLLISION", [actor.id], {"identity": actor.id, "forbidden_mutations": sorted(mutations)}, [], "Auditor holds authority outside its observer boundary.", "ROUTE_TO_STEWARD")
            if ("BUILDER" in role or str(actor.attrs.get("actor_kind") or "").upper() == "BUILDER") and "MAIN" in mutations:
                self.add("AUTHORITY_COLLISION", [actor.id], {"identity": actor.id, "forbidden_mutations": ["MAIN"]}, [], "Builder can bypass Main Steward authority.", "ROUTE_TO_STEWARD")
        if len(active_main) > 1:
            self.add("MULTIPLE_MAIN_WRITERS", [actor.id for actor in active_main], {"identity": "active-main-writers", "count": len(active_main)}, [], "More than one active Main writer exists.", "ROUTE_TO_STEWARD")

    def staleness(self) -> None:
        for node in self.graph.nodes.values():
            if node.attrs.get("stale") is True or node.attrs.get("evidence_stale") is True or node.attrs.get("version_stale") is True:
                self.add("STALE_REQUIRES_REVALIDATION", [node.id], {"identity": node.id, "observed_at": node.attrs.get("observed_at")}, [], "Evidence is stale; current truth is unknown until revalidated.", "REVALIDATE", severity="INTEGRITY_WARNING", blocking=False)

    def acceptance_integrity(self) -> None:
        illegal = {("MERGED_VERIFIED", "PUBLIC_VERIFIED"), ("PUBLIC_VERIFIED", "VALUE_EVIDENCED"), ("REVIEW_APPROVED", "HUMAN_ACCEPTED")}
        for node in self.graph.nodes.values():
            source = str(node.attrs.get("implied_from") or "").upper()
            target = str(node.attrs.get("acceptance_class") or node.attrs.get("status") or "").upper()
            if (source, target) in illegal:
                self.add("ILLEGAL_ACCEPTANCE_IMPLICATION", [node.id], {"status_pair": f"{source}->{target}"}, [], "Acceptance class was inferred from a non-equivalent class.", "ROUTE_TO_ACCEPTANCE_AUTHORITY")


def reconcile_findings(current: Sequence[Finding], prior_rows: Sequence[Mapping[str, Any]], *, full_coverage: bool, run_id: str) -> list[Finding]:
    current_by_id = {finding.finding_id: finding for finding in current}
    result = list(current)
    for row in prior_rows:
        prior = Finding(**{field.name: row.get(field.name) for field in dataclasses.fields(Finding)})
        if prior.finding_id in current_by_id:
            current_finding = current_by_id[prior.finding_id]
            if prior.state in TERMINAL_FINDING_STATES:
                current_finding.state = "REVALIDATION_PENDING"
            continue
        if prior.state in TERMINAL_FINDING_STATES:
            result.append(prior)
            continue
        if full_coverage:
            prior.state = "RESOLVED"
            prior.resolved_at = utc_now()
            prior.resolution_evidence = {"audit_run_id": run_id, "reason": "full revalidation did not reproduce finding"}
        else:
            prior.state = "REVALIDATION_PENDING"
            prior.resolution_evidence = {"audit_run_id": run_id, "reason": "audit coverage insufficient to close prior finding"}
        result.append(prior)
    return sorted(result, key=lambda finding: (finding.state in TERMINAL_FINDING_STATES, finding.severity, finding.finding_id))


def audit_summary(findings: Sequence[Finding], source_health: Mapping[str, Mapping[str, Any]], errors: Sequence[str]) -> str:
    if errors:
        return "AUDITOR_FAILURE"
    if any(value.get("required") and not value.get("available") for value in source_health.values()):
        return "INCOMPLETE_AUDIT"
    if any(finding.state in OPEN_FINDING_STATES and finding.severity in BLOCKING_SEVERITIES for finding in findings):
        return "BLOCKING_DEFECTS"
    if any(finding.state in OPEN_FINDING_STATES for finding in findings):
        return "PASS_WITH_WARNINGS"
    return "PASS"


def load_prior_state(path: Path | None) -> tuple[list[dict[str, Any]], IntegrityGraph | None, list[dict[str, Any]]]:
    if path is None or not path.exists():
        return [], None, []
    findings = load_jsonl(path / "integrity_findings.jsonl")
    graph_path = path / "integrity_graph.json"
    prior_graph = IntegrityGraph.from_snapshot(load_json(graph_path)) if graph_path.exists() else None
    index = load_jsonl(path / "integrity_run_index.jsonl")
    return findings, prior_graph, index


def execute_audit(repo: Path, catalog_path: Path, output_dir: Path, *, external_snapshots: Sequence[Path] = (), prior_state_dir: Path | None = None, event_paths: Sequence[str] = (), trigger: str = "MANUAL", baseline: bool = False) -> tuple[str, dict[str, Any]]:
    started = utc_now()
    run_id = "AUD-" + stable_hash({"started": started, "trigger": trigger, "head": os.environ.get("N0TE2_HEAD_SHA")}, 20).upper()
    errors: list[str] = []
    try:
        graph = RepositoryAdapter(repo).build()
        for snapshot_path in external_snapshots:
            merge_external_snapshot(graph, load_json(snapshot_path), snapshot_path.name)
        prior_rows, prior_graph, prior_index = load_prior_state(prior_state_dir)
        seeds = event_paths_to_seeds(graph, event_paths) if event_paths else []
        auditor = Auditor(graph, load_invariants(catalog_path), run_id, prior_graph=prior_graph, event_seeds=seeds)
        current = auditor.run()
        errors.extend(auditor.errors)
        full_coverage = not event_paths and all(not value.get("required") or value.get("available") for value in graph.source_health.values())
        findings = reconcile_findings(current, prior_rows, full_coverage=full_coverage, run_id=run_id)
        status = audit_summary(findings, graph.source_health, errors)
        remediation = [{"finding_id": f.finding_id, "owner": f.remediation_authority, "affected_cone": f.blocked_cone or f.affected_object_ids, "required_action": f.recommended_disposition, "unblock_condition": "re-run affected invariant with authoritative repair evidence"} for f in findings if f.state in OPEN_FINDING_STATES]
        completed = utc_now()
        catalog = load_invariants(catalog_path)
        receipt = {
            "run_id": run_id, "run_class": "CROSS_LEDGER_BASELINE_AUDIT" if baseline else "CROSS_LEDGER_AUDIT",
            "trigger": trigger, "started_at": started, "completed_at": completed,
            "repository_sha": os.environ.get("N0TE2_HEAD_SHA"), "authority_snapshots_consulted": auditor.consulted_sources,
            "graph_hash": graph.to_dict()["graph_hash"], "invariants_evaluated": sorted(catalog),
            "objects_scanned": len(graph.nodes), "findings_open": sum(f.state in OPEN_FINDING_STATES for f in findings),
            "checks_skipped": auditor.skipped, "external_evidence_unavailable": sorted(key for key, value in graph.source_health.items() if value.get("required") and not value.get("available")),
            "errors": errors, "integrity_status": status, "next_required_reconciliation": "daily full reconciliation or earlier affected-cone event",
        }
        index = prior_index + [{"run_id": run_id, "completed_at": completed, "trigger": trigger, "integrity_status": status, "graph_hash": receipt["graph_hash"]}]
        output_dir.mkdir(parents=True, exist_ok=True)
        dump_jsonl(output_dir / "integrity_findings.jsonl", [f.to_dict() for f in findings])
        dump_json(output_dir / "integrity_graph.json", graph.to_dict())
        dump_json(output_dir / "integrity_run_receipt.json", receipt)
        dump_jsonl(output_dir / "integrity_run_index.jsonl", index)
        dump_json(output_dir / "integrity_summary.json", {"integrity_status": status, "run_id": run_id, "open_findings": sum(f.state in OPEN_FINDING_STATES for f in findings), "blocking_findings": sum(f.state in OPEN_FINDING_STATES and f.severity in BLOCKING_SEVERITIES for f in findings)})
        dump_json(output_dir / "remediation_queue.json", {"run_id": run_id, "items": remediation})
        return status, receipt
    except Exception as exc:
        errors.append(str(exc))
        output_dir.mkdir(parents=True, exist_ok=True)
        receipt = {"run_id": run_id, "run_class": "CROSS_LEDGER_BASELINE_AUDIT" if baseline else "CROSS_LEDGER_AUDIT", "trigger": trigger, "started_at": started, "completed_at": utc_now(), "integrity_status": "AUDITOR_FAILURE", "errors": errors}
        dump_json(output_dir / "integrity_run_receipt.json", receipt)
        dump_json(output_dir / "integrity_summary.json", {"integrity_status": "AUDITOR_FAILURE", "run_id": run_id, "errors": errors})
        return "AUDITOR_FAILURE", receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="N0TE Cross-Ledger Integrity Auditor")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--catalog", default="governance/integrity_invariants.json")
    parser.add_argument("--external-snapshot", action="append", default=[])
    parser.add_argument("--prior-state-dir")
    parser.add_argument("--output-dir", default="integrity-runtime")
    parser.add_argument("--event-object", action="append", default=[])
    parser.add_argument("--event-path", action="append", default=[])
    parser.add_argument("--event-path-file")
    parser.add_argument("--trigger", default="MANUAL")
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--enforce-blocking", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    event_paths = list(args.event_path)
    if args.event_path_file:
        event_paths.extend(Path(args.event_path_file).read_text(encoding="utf-8").splitlines())
    status, receipt = execute_audit(
        repo, (repo / args.catalog).resolve() if not Path(args.catalog).is_absolute() else Path(args.catalog), Path(args.output_dir).resolve(),
        external_snapshots=[Path(path).resolve() for path in args.external_snapshot],
        prior_state_dir=Path(args.prior_state_dir).resolve() if args.prior_state_dir else None,
        event_paths=event_paths, trigger=args.trigger, baseline=args.baseline,
    )
    if args.print_summary:
        print(json.dumps({"integrity_status": status, "run_id": receipt.get("run_id"), "external_evidence_unavailable": receipt.get("external_evidence_unavailable", []), "errors": receipt.get("errors", [])}, sort_keys=True))
    if status == "AUDITOR_FAILURE":
        return 2
    if args.enforce_blocking and status == "BLOCKING_DEFECTS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
