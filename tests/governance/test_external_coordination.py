import json
from pathlib import Path

from governance.external_coordination import (
    canonical_digest,
    changed,
    compact_continue_snapshot,
    normalize_runtime_actor,
    operation_state,
    select_executor,
)


ROOT = Path(__file__).resolve().parents[2]


def load_json(name: str):
    return json.loads((ROOT / "governance" / name).read_text(encoding="utf-8"))


def test_operating_layers_keep_product_builder_and_cloud_plane_separate():
    data = load_json("operating_layers.json")
    layers = {row["class"]: row for row in data["layers"]}
    assert "EMBEDDED_ARTIST_HQ_SIBLING" in layers
    assert "CLOUD_COORDINATION_PLANE" in layers
    assert "TEMPORARY_CONSTRUCTION_MACHINERY" in layers
    assert layers["EMBEDDED_ARTIST_HQ_SIBLING"]["persistent"] is True
    assert layers["TEMPORARY_CONSTRUCTION_MACHINERY"]["persistent"] is False
    assert "N0TE product semantics" in layers["TEMPORARY_CONSTRUCTION_MACHINERY"]["does_not_own"]
    assert any("Foreground outcomes" in rule for rule in data["scope_invariants"])


def test_external_capability_matrix_is_not_a_product_capability_system():
    data = load_json("executor_capabilities.json")
    assert data["scope"] == "EXTERNAL_COORDINATION_ONLY"
    assert data["not_product_capabilities"] is True
    assert data["security"]["store_secrets"] is False


def test_runtime_state_does_not_infer_scheduler_from_semantic_active():
    actor = {"id": "x", "lifecycle": {"state": "ACTIVE"}}
    normalized = normalize_runtime_actor(actor, None)
    assert normalized.semantic_lifecycle == "ACTIVE"
    assert normalized.scheduler_enabled is None
    assert normalized.health == "UNOBSERVED_RUNTIME"


def test_runtime_observation_can_show_active_semantics_but_disabled_scheduler():
    actor = {"id": "x", "lifecycle": {"state": "ACTIVE"}}
    normalized = normalize_runtime_actor(
        actor,
        {"scheduler_enabled": False, "health": "NOT_RUNNING", "observed_at": "2026-09-02"},
    )
    assert normalized.semantic_lifecycle == "ACTIVE"
    assert normalized.scheduler_enabled is False
    assert normalized.health == "NOT_RUNNING"


def test_continue_snapshot_digest_suppresses_unchanged_loops():
    snap = compact_continue_snapshot(
        work_id="UX-01-CONTEXT-LIFECYCLE-01",
        state="BLOCKED",
        owner="N0TE-BUILD-HARNESS",
        blocker="windows-test",
        next_action="repair exact failing test",
        evidence_basis={"head": "abc", "run": 1},
    )
    assert changed(None, snap) is True
    assert changed(snap["change_digest"], snap) is False
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})


def test_executor_selection_prefers_observed_working_and_preserves_limits():
    matrix = load_json("executor_capabilities.json")
    assert operation_state(matrix, "CHAT_GITHUB_CONNECTOR", "file_read") == "OBSERVED_WORKING"
    choice = select_executor(matrix, "file_read", ["CHAT_GITHUB_CONNECTOR", "LOCAL_GH"])
    assert choice["surface"] == "CHAT_GITHUB_CONNECTOR"
    assert choice["state"] == "OBSERVED_WORKING"

    limited = select_executor(
        matrix,
        "workflow_job_log_read",
        ["CHAT_GITHUB_CONNECTOR", "LOCAL_GH"],
    )
    assert limited["surface"] == "CHAT_GITHUB_CONNECTOR"
    assert limited["state"] == "OBSERVED_LIMITED"
