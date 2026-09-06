from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "governance" / "check_steward_integrity.py"
spec = importlib.util.spec_from_file_location("check_steward_integrity", MODULE_PATH)
assert spec is not None and spec.loader is not None
integrity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integrity)


def test_repository_cross_ledger_integrity() -> None:
    integrity.run(ROOT, verify_git=False)


def _requirements() -> dict:
    return {
        "sequence": {"start": 2, "end": 4},
        "canonical_scope": {"start": 2, "end": 4, "retained_requirement_count": 3},
        "canonical_extensions": [],
        "held_or_boundary": ["REQ-SCOPE-004"],
        "superseded": [],
    }


def _graph() -> dict:
    return {
        "nodes": [
            {"id": "CORE", "state": "PRESERVED", "requirements": "002-003", "depends_on": [], "any_of": []},
            {"id": "LATER-01", "state": "PRESERVED", "requirements": "004", "depends_on": [], "any_of": []},
        ]
    }


def test_held_requirement_cannot_silently_disappear() -> None:
    graph = _graph()
    graph["nodes"][1]["requirements"] = ""
    with pytest.raises(integrity.StewardIntegrityError, match="held_or_boundary and LATER-01 diverged"):
        integrity.validate_requirement_graph(_requirements(), graph)


def test_graph_requirement_cannot_escape_canonical_scope() -> None:
    graph = _graph()
    graph["nodes"][0]["requirements"] = "002,999"
    with pytest.raises(integrity.StewardIntegrityError, match="outside canonical scope"):
        integrity.validate_requirement_graph(_requirements(), graph)


def test_superseded_decision_requires_successor() -> None:
    with pytest.raises(integrity.StewardIntegrityError, match="lacks durable successor"):
        integrity.validate_decisions([{"id": "DEC-1", "status": "SUPERSEDED", "supersedes": []}])


def test_decision_supersession_cycle_is_rejected() -> None:
    rows = [
        {"id": "DEC-1", "status": "ACTIVE", "supersedes": ["DEC-2"]},
        {"id": "DEC-2", "status": "ACTIVE", "supersedes": ["DEC-1"]},
    ]
    with pytest.raises(integrity.StewardIntegrityError, match="cycle"):
        integrity.validate_decisions(rows)


def _receipt() -> dict:
    return {
        "status": "ACTIVE",
        "node_id": "INCIDENT-REPAIR",
        "increment_id": "INCIDENT-REPAIR-TEST",
        "baseline_sha": "a" * 40,
        "lineage": {"requirements": []},
        "incident_repair_ids": ["INC-1"],
    }


def _current() -> dict:
    return {"lifecycle_state": "ACTIVE", "active_node": "INCIDENT-REPAIR", "active_increment": "INCIDENT-REPAIR-TEST"}


def _incidents() -> dict:
    return {"INC-1": {"id": "INC-1", "status": "OPEN_REPAIRING", "repair_contract": {"future_receipt_field": "incident_repair_ids"}}}


def test_public_handoff_obligation_cannot_be_orphaned() -> None:
    receipt = _receipt()
    receipt["public_handoff_required"] = True
    with pytest.raises(integrity.StewardIntegrityError, match="no durable public_handoff_ref"):
        integrity.validate_receipt_and_current_state(_current(), receipt, {"REQ-SCOPE-002"}, set(), {}, _incidents())


def test_merge_receipt_cannot_claim_public_verified() -> None:
    receipt = _receipt()
    receipt["public_acceptance_state"] = "PUBLIC_VERIFIED"
    with pytest.raises(integrity.StewardIntegrityError, match="cannot create PUBLIC_VERIFIED"):
        integrity.validate_receipt_and_current_state(_current(), receipt, {"REQ-SCOPE-002"}, set(), {}, _incidents())


def test_equivalence_requires_semantic_proof() -> None:
    receipt = _receipt()
    receipt["current_disposition"] = "SUPERSEDED_BY_EQUIVALENT"
    with pytest.raises(integrity.StewardIntegrityError, match="requires equivalence_receipt"):
        integrity.validate_receipt_and_current_state(_current(), receipt, {"REQ-SCOPE-002"}, set(), {}, _incidents())


def test_equivalence_receipt_with_complete_lineage_passes() -> None:
    integrity.validate_equivalence_receipt({
        "original_requirement": "REQ-SCOPE-002",
        "replacement_implementation": "impl://replacement",
        "semantic_coverage_mapping": {"REQ-SCOPE-002": ["impl://replacement"]},
        "retained_behavior": ["A"],
        "changed_behavior": [],
        "uncovered_residue": [],
        "acceptance_evidence": ["evidence://1"],
        "semantic_authority": "USER_ROOT",
        "lineage": ["receipt://old", "receipt://new"],
        "successor_status": "INTEGRATED"
    }, {"REQ-SCOPE-002"})


def test_resolved_incident_cannot_authorize_active_repair() -> None:
    incidents = _incidents()
    incidents["INC-1"]["status"] = "RESOLVED"
    with pytest.raises(integrity.StewardIntegrityError, match="non-open incident"):
        integrity.validate_receipt_and_current_state(_current(), _receipt(), {"REQ-SCOPE-002"}, set(), {}, incidents)


def test_active_receipt_must_bind_exact_current_increment() -> None:
    current = _current()
    current["active_increment"] = "OTHER"
    with pytest.raises(integrity.StewardIntegrityError, match="increment diverges"):
        integrity.validate_receipt_and_current_state(current, _receipt(), {"REQ-SCOPE-002"}, set(), {}, _incidents())
