from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "governance" / "integrity_auditor.py"
SPEC = importlib.util.spec_from_file_location("integrity_auditor", MODULE_PATH)
assert SPEC and SPEC.loader
ia = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ia
SPEC.loader.exec_module(ia)
CATALOG = ia.load_invariants(ROOT / "governance" / "integrity_invariants.json")


def graph(nodes=(), edges=(), health=None):
    return ia.IntegrityGraph.from_snapshot({
        "nodes": list(nodes),
        "edges": list(edges),
        "source_health": health or {"LOCAL": {"available": True, "required": True}},
    })


def node(node_id, kind, **attrs):
    return {"id": node_id, "kind": kind, "attrs": attrs, "source": "TEST"}


def edge(src, rel, dst, **attrs):
    return {"src": src, "rel": rel, "dst": dst, "attrs": attrs, "source": "TEST"}


def audit(g, prior=None, event_seeds=()):
    a = ia.Auditor(g, CATALOG, "AUD-TEST", prior_graph=prior, event_seeds=event_seeds)
    return a, a.run()


def ids(findings):
    return {f.invariant_id for f in findings}


def test_01_accepted_requirement_disappears_without_disposition():
    prior = graph([node("REQ-SCOPE-001", "REQUIREMENT", accepted=True, disposition="RETAINED")])
    current = graph([])
    _, findings = audit(current, prior=prior)
    assert "DISAPPEARED_REQUIREMENT" in ids(findings)


def test_02_blocked_requirement_remains_tracked_not_orphaned():
    g = graph([node("REQ-SCOPE-001", "REQUIREMENT", accepted=True, disposition="BLOCKED")])
    _, findings = audit(g)
    assert "ORPHAN_REQUIREMENT_NO_STATE" not in ids(findings)
    assert "REQ-SCOPE-001" in g.nodes


def test_03_stale_branch_causes_orphan_risk_not_requirement_deletion():
    g = graph(
        [
            node("REQ-SCOPE-001", "REQUIREMENT", accepted=True, disposition="RETAINED"),
            node("CAND:stale", "CANDIDATE", status="STALE"),
        ],
        [edge("CAND:stale", "SERVES", "REQ-SCOPE-001")],
    )
    _, findings = audit(g)
    assert "ORPHAN_REQUIREMENT_AFTER_BRANCH_STALE" in ids(findings)
    assert "REQ-SCOPE-001" in g.nodes


def test_04_merged_candidate_lacks_merge_receipt():
    g = graph([node("CAND:1", "CANDIDATE", status="MERGED_VERIFIED", head_sha="a" * 40)])
    _, findings = audit(g)
    assert "MERGED_WITHOUT_MERGE_RECEIPT" in ids(findings)


def test_05_merge_receipt_references_wrong_head():
    g = graph(
        [
            node("CAND:1", "CANDIDATE", status="MERGED_VERIFIED", head_sha="a" * 40),
            node("MR:1", "MERGE_RECEIPT", subject_id="CAND:1", head_sha="b" * 40),
        ],
        [edge("MR:1", "VERIFIED_BY", "CAND:1")],
    )
    _, findings = audit(g)
    assert "MERGE_RECEIPT_HEAD_MISMATCH" in ids(findings)


def test_06_public_facing_merge_lacks_handoff():
    g = graph([node("CAND:1", "CANDIDATE", status="MERGED_VERIFIED", public_consequence=True, requires_merge_receipt=False)])
    _, findings = audit(g)
    assert "MERGED_PUBLIC_CHANGE_WITHOUT_HANDOFF" in ids(findings)


def test_07_orphan_public_handoff_lacks_implementation_lineage():
    g = graph([node("PH:1", "PUBLIC_HANDOFF", status="READY")])
    _, findings = audit(g)
    assert "ORPHAN_PUBLIC_HANDOFF" in ids(findings)


def test_08_public_pass_lacks_observation():
    g = graph([node("PUB:1", "PUBLIC_DEPLOYMENT", status="PASS")])
    _, findings = audit(g)
    assert "PUBLIC_PASS_WITHOUT_OBSERVATION" in ids(findings)


def test_09_rights_sensitive_asset_lacks_rights_evidence():
    g = graph([node("ASSET:1", "ASSET_VERSION", rights_sensitive=True, version="v1")])
    _, findings = audit(g)
    assert "RIGHTS_REQUIRED_EVIDENCE_MISSING" in ids(findings)


def test_10_rights_evidence_references_wrong_asset_version():
    g = graph(
        [
            node("ASSET:1", "ASSET_VERSION", rights_sensitive=True, version="v2"),
            node("RIGHTS:1", "RIGHTS_EVIDENCE", object_version="v1"),
        ],
        [edge("ASSET:1", "RIGHTS_PROVEN_BY", "RIGHTS:1")],
    )
    _, findings = audit(g)
    assert "RIGHTS_EVIDENCE_VERSION_MISMATCH" in ids(findings)


def test_11_expired_rights_evidence():
    g = graph(
        [
            node("ASSET:1", "ASSET_VERSION", rights_sensitive=True, version="v1"),
            node("RIGHTS:1", "RIGHTS_EVIDENCE", object_version="v1", expired=True),
        ],
        [edge("ASSET:1", "RIGHTS_PROVEN_BY", "RIGHTS:1")],
    )
    _, findings = audit(g)
    assert "RIGHTS_EVIDENCE_EXPIRED" in ids(findings)


def test_12_supersession_lacks_equivalence_receipt():
    g = graph([
        node("REQ:old", "REQUIREMENT", accepted=True, disposition="SUPERSEDED_WITH_PROOF"),
        node("REQ:new", "REQUIREMENT", accepted=True, disposition="RETAINED"),
        node("SUP:1", "SUPERSESSION", old_id="REQ:old", new_id="REQ:new", status="SUPERSEDED_BY_EQUIVALENT"),
    ], [edge("REQ:new", "SERVES", "REQ:new")])
    _, findings = audit(g)
    assert "SUPERSESSION_WITHOUT_EQUIVALENCE_RECEIPT" in ids(findings)


def test_13_partial_supersession_drops_residue():
    g = graph([
        node("REQ:old", "REQUIREMENT", accepted=True, disposition="SUPERSEDED_WITH_PROOF"),
        node("REQ:new", "REQUIREMENT", accepted=True, disposition="RETAINED"),
        node("SUP:1", "SUPERSESSION", old_id="REQ:old", new_id="REQ:new", partial=True),
    ], [edge("REQ:new", "SERVES", "REQ:new")])
    _, findings = audit(g)
    assert "PARTIAL_EQUIVALENCE_WITH_DROPPED_RESIDUE" in ids(findings)


def test_14_supersession_cycle():
    g = graph([
        node("REQ:A", "REQUIREMENT", accepted=True, disposition="SUPERSEDED_WITH_PROOF"),
        node("REQ:B", "REQUIREMENT", accepted=True, disposition="SUPERSEDED_WITH_PROOF"),
        node("SUP:1", "SUPERSESSION", old_id="REQ:A", new_id="REQ:B"),
        node("SUP:2", "SUPERSESSION", old_id="REQ:B", new_id="REQ:A"),
    ])
    _, findings = audit(g)
    assert "SUPERSESSION_CYCLE" in ids(findings)


def test_15_canonical_status_contradiction():
    g = graph([node("CAND:1", "CANDIDATE", status="MERGED_VERIFIED", present_on_main=False, requires_merge_receipt=False)])
    _, findings = audit(g)
    assert "CANONICAL_STATUS_CONTRADICTION" in ids(findings)


def test_16_completion_claim_lacks_required_acceptance():
    g = graph([node("CR:1", "COMPLETION_RECEIPT", subject_id="OBJ:1", implementation_evidence=True, acceptance_required=True)])
    _, findings = audit(g)
    assert "COMPLETION_WITHOUT_REQUIRED_ACCEPTANCE" in ids(findings)


def test_17_stale_evidence_requires_revalidation_not_failure():
    g = graph([node("OBS:1", "OBSERVATION", stale=True, observed_at="2026-09-01T00:00:00Z")])
    _, findings = audit(g)
    stale = [f for f in findings if f.invariant_id == "STALE_REQUIRES_REVALIDATION"]
    assert stale
    assert stale[0].severity == "INTEGRITY_WARNING"
    assert stale[0].recommended_disposition == "REVALIDATE"


def test_18_multiple_main_writers_detected():
    g = graph([
        node("ACTOR:1", "AUTONOMOUS_ACTOR", state="ACTIVE", role_class="MAIN_WRITER", authority="MAIN_WRITER", allowed_mutations=["MAIN"]),
        node("ACTOR:2", "AUTONOMOUS_ACTOR", state="ACTIVE", role_class="MAIN_WRITER", authority="MAIN_WRITER", allowed_mutations=["MAIN"]),
    ])
    _, findings = audit(g)
    assert "MULTIPLE_MAIN_WRITERS" in ids(findings)


def test_19_auditor_cannot_mutate_semantic_authority():
    g = graph([node("ACTOR:AUD", "AUTONOMOUS_ACTOR", state="ACTIVE", role_class="INTEGRITY_AUDITOR", allowed_mutations=["SEMANTIC_SCOPE"])])
    _, findings = audit(g)
    assert "AUTHORITY_COLLISION" in ids(findings)


def test_20_builder_cannot_use_auditor_to_bypass_steward():
    g = graph([node("ACTOR:BUILDER", "AUTONOMOUS_ACTOR", state="ACTIVE", actor_kind="BUILDER", role_class="BUILDER", authority="BUILD", allowed_mutations=["MAIN"])])
    _, findings = audit(g)
    assert "AUTHORITY_COLLISION" in ids(findings)


def test_21_resolved_finding_remains_durably_visible():
    g = graph([node("ASSET:1", "ASSET_VERSION", rights_sensitive=True, version="v1")])
    _, current = audit(g)
    assert current
    prior = [current[0].to_dict()]
    reconciled = ia.reconcile_findings([], prior, full_coverage=True, run_id="AUD-2")
    assert len(reconciled) == 1
    assert reconciled[0].state == "RESOLVED"
    assert reconciled[0].resolution_evidence["audit_run_id"] == "AUD-2"


def test_22_localized_defect_does_not_block_unrelated_work():
    g = graph([
        node("CAND:A", "CANDIDATE", status="MERGED_VERIFIED"),
        node("REQ:A", "REQUIREMENT", accepted=True, disposition="RETAINED"),
        node("CAND:B", "CANDIDATE", status="ACTIVE", requires_merge_receipt=False),
        node("REQ:B", "REQUIREMENT", accepted=True, disposition="RETAINED"),
    ], [edge("CAND:A", "SERVES", "REQ:A"), edge("CAND:B", "SERVES", "REQ:B")])
    _, findings = audit(g)
    merged = next(f for f in findings if f.invariant_id == "MERGED_WITHOUT_MERGE_RECEIPT")
    assert "CAND:A" in merged.blocked_cone
    assert "REQ:A" in merged.blocked_cone
    assert "CAND:B" not in merged.blocked_cone
    assert "REQ:B" not in merged.blocked_cone


def test_historical_requirement_range_migration_parser():
    assert ia.parse_requirement_spec("002-003,109,140-143") == {
        "REQ-SCOPE-002", "REQ-SCOPE-003", "REQ-SCOPE-109", "REQ-SCOPE-140", "REQ-SCOPE-141", "REQ-SCOPE-142", "REQ-SCOPE-143"
    }


def test_malformed_jsonl_is_not_silently_accepted(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"id":"ok"}\n{bad json}\n', encoding="utf-8")
    with pytest.raises(ia.IntegrityError):
        ia.load_jsonl(path)


def test_missing_required_external_evidence_cannot_report_global_pass():
    findings = []
    status = ia.audit_summary(findings, {
        "LOCAL": {"available": True, "required": True},
        "PUBLIC_RUNTIME": {"available": False, "required": True},
    }, [])
    assert status == "INCOMPLETE_AUDIT"


def test_false_positive_resolution_requires_explicit_history_not_disappearance():
    f = ia.Finding(
        finding_id="FND-X", invariant_id="STALE_REQUIRES_REVALIDATION", severity="INTEGRITY_WARNING",
        detected_at="2026-09-05T00:00:00Z", audit_run_id="AUD-1", affected_object_ids=["OBS:1"],
        authoritative_sources_consulted=["TEST"], conflicting_or_missing_edges=[], exact_evidence={},
        current_consequence="verify", blocked_cone=[], remediation_authority="EVIDENCE_OWNER",
        recommended_disposition="REVALIDATE", related_receipt_ids=[], trace_ids=[], freshness={}, state="FALSE_POSITIVE_WITH_PROOF",
        resolution_evidence={"proof":"x"}, resolved_at="2026-09-05T01:00:00Z")
    reconciled = ia.reconcile_findings([], [f.to_dict()], full_coverage=True, run_id="AUD-2")
    assert reconciled[0].state == "FALSE_POSITIVE_WITH_PROOF"


def test_registered_auditor_actor_is_read_mostly_and_supervised():
    registry = json.loads((ROOT / "governance" / "automation_registry.json").read_text(encoding="utf-8"))
    actor = next(row for row in registry["actors"] if row["id"] == "AUTO-CROSS-LEDGER-INTEGRITY-001")
    assert actor["parent"] == "N0TE-SUPERVISOR"
    assert actor["reports_to"] == "N0TE-SUPERVISOR"
    assert actor["lifecycle"]["state"] == "DORMANT"
    assert actor["observability"]["reactivation_is_event"] is True
    forbidden = {"MAIN", "SEMANTIC_SCOPE", "PUBLIC_CANON", "PROVIDER_ACCOUNT", "RIGHTS_DECLARATION", "HUMAN_ACCEPTANCE"}
    assert forbidden.isdisjoint(set(actor["allowed_mutations"]))


def test_integrity_workflow_has_no_repository_write_authority():
    text = (ROOT / ".github" / "workflows" / "integrity-auditor.yml").read_text(encoding="utf-8")
    assert "contents: read" in text
    assert "actions: read" in text
    assert "contents: write" not in text
    assert "statuses: write" not in text
    assert "checks: write" not in text
    assert "persist-credentials: false" in text


def test_global_source_contract_requires_public_provider_rights_and_acceptance_truth():
    doc = json.loads((ROOT / "governance" / "integrity_sources.json").read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in doc["sources"]}
    for source_id in ("TELLMEN0TE_PUBLIC_RUNTIME", "PROVIDER_EVIDENCE", "RIGHTS_PROVENANCE_EVIDENCE", "HUMAN_ACCEPTANCE_EVIDENCE"):
        assert by_id[source_id]["required_for_global_pass"] is True
        assert by_id[source_id]["absence_state"] == "INCOMPLETE_AUDIT"


def test_repository_adapter_preserves_mapped_unselected_extensions_and_completion_graph_role(tmp_path):
    gov = tmp_path / "governance"
    gov.mkdir()
    (gov / "requirements.json").write_text(json.dumps({
        "canonical_scope": {"start": 154, "end": 154, "source_revision": "x"},
        "canonical_extensions": [{"id": "REQ-SCOPE-154", "state": "MAPPED", "selected": False}],
        "held_or_boundary": [], "superseded": []
    }), encoding="utf-8")
    (gov / "completion_graph.json").write_text(json.dumps({
        "nodes": [{"id": "UX-01", "state": "DONE", "requirements": "154", "depends_on": []}]
    }), encoding="utf-8")
    (gov / "current_state.json").write_text("{}", encoding="utf-8")
    (gov / "active_receipt.json").write_text("{}", encoding="utf-8")
    (gov / "automation_registry.json").write_text('{"actors":[]}', encoding="utf-8")
    (gov / "authority.json").write_text("{}", encoding="utf-8")
    (gov / "invariants.json").write_text("{}", encoding="utf-8")
    g = ia.RepositoryAdapter(tmp_path).build()
    assert g.nodes["REQ-SCOPE-154"].attrs["disposition"] == "MAPPED_UNSELECTED"
    assert g.nodes["CONSTRUCTION:UX-01"].kind == "CONSTRUCTION_STATE"
    _, findings = audit(g)
    assert "ORPHAN_REQUIREMENT_NO_STATE" not in ids(findings)
    assert "MERGED_WITHOUT_MERGE_RECEIPT" not in ids(findings)
