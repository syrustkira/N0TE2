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

if active != "CORE-01" or increment != "CORE-01E":
    print(f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}", file=sys.stderr)
    raise SystemExit(1)

from n0te2 import HeadquartersMemory  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    hq = HeadquartersMemory.create(td, "Smoke Artist")
    profile = hq.store.profile_id
    song = hq.store.create_song("Smoke Song")
    asset = hq.store.attach_asset(song.id, name="take.wav", sha256="f" * 64)
    asset_record = hq.provenance.record(
        output_kind="ASSET",
        output_id=asset.id,
        input_kind="EXTERNAL",
        input_ref="file:///recordings/take.wav",
        operation="IMPORTED",
        evidence_source_kind="OBSERVED",
        evidence_ref="import:1",
    )
    v1 = hq.store.create_version(song.id, label="v1", asset_ids=[asset.id])
    hq.provenance.record(
        output_kind="VERSION",
        output_id=v1.id,
        input_kind="ASSET",
        input_ref=asset.id,
        operation="ASSEMBLED",
        evidence_source_kind="USER_DECLARED",
    )
    v2 = hq.store.create_version(song.id, label="v2", parent_version_id=v1.id, asset_ids=[asset.id])
    checkpoint = hq.activity.checkpoint()
    transform = hq.provenance.record(
        output_kind="VERSION",
        output_id=v2.id,
        input_kind="VERSION",
        input_ref=v1.id,
        operation="TRANSFORMED",
        tool_ref="tool:owned-compressor",
        model_ref="model:mix-assistant-v1",
        recipe_ref="recipe:chorus-lift-2",
        evidence_source_kind="USER_DECLARED",
        evidence_ref="session:42",
    )
    events = hq.activity.for_song(song.id, after_sequence=checkpoint)
    assert len(events) == 1 and events[0].event_type == "PROVENANCE_RECORDED"
    assert events[0].object_id == v2.id and events[0].payload["provenance_id"] == transform.id
    hq.close()

    hq = HeadquartersMemory.open(td, profile)
    conn = hq.store._conn
    tables = ("metadata", "songs", "versions", "assets", "provenance_records", "activity_events")
    before = {table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]) for table in tables}
    explanation = hq.provenance.explain_version(v2.id)
    after = {table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]) for table in tables}
    assert before == after
    assert explanation.parent_version_id == v1.id
    assert [item.id for item in explanation.attached_assets] == [asset.id]
    assert explanation.attached_assets[0].records[0].id == asset_record.id
    assert explanation.derivations[0].id == transform.id
    assert explanation.derivations[0].input_ref == v1.id
    assert explanation.derivations[0].tool_ref == "tool:owned-compressor"
    assert explanation.derivations[0].model_ref == "model:mix-assistant-v1"
    assert explanation.derivations[0].rights_ref is None
    assert explanation.derivations[0].cost_ref is None
    hq.close()

print("CORE-01E CONSUMER SMOKE: GREEN: Explain Version preserves immutable source/derivation provenance without inventing missing rights or cost evidence")
