#!/usr/bin/env python3
"""Stage-aware construction smoke for the active bounded consumer outcome."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))

state = json.loads((repo / "governance/current_state.json").read_text())
active = state["active_node"]
increment = state.get("active_increment")

if active in {"BOOT-02", "LEGACY-01"}:
    for forbidden in ("app", "src", "n0te2", "legacy"):
        path = repo / forbidden
        if path.exists() and any(path.rglob("*")):
            print(f"PRE-PRODUCT SMOKE: RED: product implementation appeared early: {forbidden}/", file=sys.stderr)
            raise SystemExit(1)
    print("PRE-PRODUCT SMOKE: GREEN")
    raise SystemExit(0)

if active != "CORE-01" or increment != "CORE-01G":
    print(f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}", file=sys.stderr)
    raise SystemExit(1)

from n0te2 import HeadquartersMemory  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    hq = HeadquartersMemory.create(td, "Smoke Artist")
    profile = hq.store.profile_id

    song_a = hq.store.create_song("Smoke Song A")
    asset_a = hq.store.attach_asset(
        song_a.id, name="take.wav", sha256="a" * 64, source_uri="file:///songs/a/take.wav"
    )
    v1 = hq.store.create_version(song_a.id, label="v1", asset_ids=[asset_a.id])
    hq.store.approve_version(song_a.id, v1.id)
    v2 = hq.store.create_version(
        song_a.id, label="v2", parent_version_id=v1.id, asset_ids=[asset_a.id]
    )

    old = hq.evidence.record_claim(
        scope_kind="SONG",
        scope_id=song_a.id,
        key="chorus.energy",
        value="needs lift",
        source_kind="OBSERVED",
        source_ref="listen:1",
        confidence=0.7,
    )
    conflicting = hq.evidence.record_claim(
        scope_kind="SONG",
        scope_id=song_a.id,
        key="chorus.energy",
        value="already right",
        source_kind="INFERRED",
        source_ref="analysis:1",
        confidence=0.55,
    )
    reconciled = hq.evidence.reconcile_for_song(
        song_id=song_a.id,
        key="chorus.energy",
        value="needs lift",
        source_kind="USER_DECLARED",
        source_ref="artist-confirmation",
    )

    external = hq.provenance.record(
        output_kind="ASSET",
        output_id=asset_a.id,
        input_kind="EXTERNAL",
        input_ref="file:///imports/original-take.wav",
        operation="IMPORTED",
        evidence_source_kind="OBSERVED",
        evidence_ref="import:1",
    )
    derived = hq.provenance.record(
        output_kind="VERSION",
        output_id=v2.id,
        input_kind="VERSION",
        input_ref=v1.id,
        operation="TRANSFORMED",
        evidence_source_kind="USER_DECLARED",
        evidence_ref="session:42",
    )

    song_b = hq.store.create_song("Smoke Song B")
    asset_b = hq.store.attach_asset(song_b.id, name="b.wav", sha256="b" * 64)
    vb = hq.store.create_version(song_b.id, label="b1", asset_ids=[asset_b.id])
    claim_b = hq.evidence.record_claim(
        scope_kind="SONG",
        scope_id=song_b.id,
        key="private.to.b",
        value=True,
        source_kind="USER_DECLARED",
    )
    prov_b = hq.provenance.record(
        output_kind="VERSION",
        output_id=vb.id,
        input_kind="EXTERNAL",
        input_ref="file:///imports/b.wav",
        operation="IMPORTED",
        evidence_source_kind="OBSERVED",
    )
    hq.close()

    hq = HeadquartersMemory.open(td, profile)
    conn = hq.store._conn
    tables = (
        "metadata",
        "songs",
        "versions",
        "assets",
        "version_assets",
        "evidence_claims",
        "evidence_supersessions",
        "activity_events",
        "provenance_records",
    )
    before = {table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]) for table in tables}
    before_changes = conn.total_changes
    map_a = hq.knowledge.for_song(song_a.id)
    repeated = hq.knowledge.for_song(song_a.id)
    after = {table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]) for table in tables}

    assert map_a == repeated
    assert before == after and before_changes == conn.total_changes
    assert [edge.target_id for edge in map_a.edges_of_kind("CURRENT_VERSION")] == [v2.id]
    assert [edge.target_id for edge in map_a.edges_of_kind("APPROVED_VERSION")] == [v1.id]
    assert any(
        edge.kind == "VERSION_PARENT" and edge.source_id == v2.id and edge.target_id == v1.id
        for edge in map_a.edges
    )
    assert map_a.node("ASSET", asset_a.id) is not None
    assert map_a.node("EVIDENCE_CLAIM", old.id) is not None
    assert map_a.node("EVIDENCE_CLAIM", conflicting.id) is not None
    assert map_a.node("EVIDENCE_CLAIM", reconciled.id) is not None
    assert {
        (edge.source_id, edge.target_id)
        for edge in map_a.edges_of_kind("EVIDENCE_SUPERSEDES")
        if edge.source_id == reconciled.id
    } == {(reconciled.id, old.id), (reconciled.id, conflicting.id)}
    assert any(
        node.kind == "EXTERNAL_REF" and node.data["ref"] == "file:///imports/original-take.wav"
        for node in map_a.nodes
    )
    assert any(
        edge.kind == "DERIVED_FROM" and edge.source_id == derived.id and edge.target_id == v1.id
        for edge in map_a.edges
    )
    assert any(
        edge.kind == "PROVENANCE_DESCRIBES" and edge.source_id == external.id and edge.target_id == asset_a.id
        for edge in map_a.edges
    )
    forbidden_ids = {song_b.id, asset_b.id, vb.id, claim_b.id, prov_b.id}
    assert not any(node.id in forbidden_ids for node in map_a.nodes)
    hq.close()

print("CORE-01G CONSUMER SMOKE: GREEN: one deterministic pure-read Song map preserves current/approved, history, provenance and cross-Song isolation")
