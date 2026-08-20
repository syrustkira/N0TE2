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

if active != "CORE-01" or increment != "CORE-01H":
    print(f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}", file=sys.stderr)
    raise SystemExit(1)

from n0te2 import HeadquartersMemory  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    hq = HeadquartersMemory.create(td, "Smoke Artist")
    profile = hq.store.profile_id
    song_a = hq.store.create_song("Smoke Song A")
    asset = hq.store.attach_asset(song_a.id, name="take.wav", sha256="a" * 64)
    v1 = hq.store.create_version(song_a.id, label="v1", asset_ids=[asset.id])
    hq.store.approve_version(song_a.id, v1.id)
    v2 = hq.store.create_version(
        song_a.id, label="v2", parent_version_id=v1.id, asset_ids=[asset.id]
    )

    technical = hq.evidence.record_claim(
        scope_kind="VERSION",
        scope_id=v2.id,
        key="timing.feel",
        value="off-grid",
        source_kind="MEASURED",
        source_ref="analysis:timing",
        twin_domain="TECHNICAL",
    )
    creative = hq.evidence.record_claim(
        scope_kind="SONG",
        scope_id=song_a.id,
        key="timing.feel",
        value="intentional push",
        source_kind="USER_DECLARED",
        source_ref="artist:intent",
        twin_domain="CREATIVE",
    )
    unspecified = hq.evidence.record_claim(
        scope_kind="SONG",
        scope_id=song_a.id,
        key="legacy.note",
        value="classify later",
        source_kind="REMEMBERED",
    )

    song_b = hq.store.create_song("Smoke Song B")
    claim_b = hq.evidence.record_claim(
        scope_kind="SONG",
        scope_id=song_b.id,
        key="private.to.b",
        value=True,
        source_kind="USER_DECLARED",
        twin_domain="CREATIVE",
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
    before = {
        table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        for table in tables
    }
    before_changes = conn.total_changes

    view = hq.twins.for_song(song_id=song_a.id, version_id=v2.id)
    repeated = hq.twins.for_song(song_id=song_a.id, version_id=v2.id)
    graph = hq.knowledge.for_song(song_a.id)

    after = {
        table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        for table in tables
    }

    assert view == repeated
    assert before == after and before_changes == conn.total_changes
    assert technical in view.technical_claims
    assert creative in view.creative_claims
    assert unspecified in view.unspecified_claims
    assert len(view.conflicts) == 1
    conflict = view.conflicts[0]
    assert conflict.key == "timing.feel"
    assert set(conflict.claim_ids) == {technical.id, creative.id}
    assert not hasattr(conflict, "winner")
    assert hq.evidence.get_claim(technical.id).source_kind == "MEASURED"
    assert hq.evidence.get_claim(creative.id).source_kind == "USER_DECLARED"
    assert graph.node("EVIDENCE_CLAIM", technical.id).data["twin_domain"] == "TECHNICAL"
    assert graph.node("EVIDENCE_CLAIM", creative.id).data["twin_domain"] == "CREATIVE"
    assert graph.node("EVIDENCE_CLAIM", unspecified.id).data["twin_domain"] == "UNSPECIFIED"
    assert graph.node("EVIDENCE_CLAIM", claim_b.id) is None
    restored = hq.store.get_song(song_a.id)
    assert restored.current_version_id == v2.id
    assert restored.approved_version_id == v1.id
    hq.close()

print("CORE-01H CONSUMER SMOKE: GREEN: Technical and Creative Twin evidence survive restart, stay separate, surface conflict, and write no state during review")
