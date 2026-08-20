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

if active != "CORE-01" or increment != "CORE-01C":
    print(f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}", file=sys.stderr)
    raise SystemExit(1)

from n0te2 import HeadquartersMemory  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    hq = HeadquartersMemory.create(td, "Smoke Artist")
    profile_id = hq.store.profile_id
    song_a = hq.store.create_song("Smoke Song A")
    song_b = hq.store.create_song("Smoke Song B")
    asset = hq.store.attach_asset(song_a.id, name="source.wav", sha256="f" * 64)
    v1 = hq.store.create_version(song_a.id, label="v1", asset_ids=[asset.id])
    hq.store.approve_version(song_a.id, v1.id)
    checkpoint = hq.activity.checkpoint()
    v2 = hq.store.create_version(song_a.id, label="v2", parent_version_id=v1.id, asset_ids=[asset.id])
    first = hq.evidence.record_claim(
        scope_kind="SONG", scope_id=song_a.id, key="chorus.energy", value="needs lift", source_kind="USER_DECLARED"
    )
    second = hq.evidence.record_claim(
        scope_kind="SONG", scope_id=song_a.id, key="chorus.energy", value="already right", source_kind="INFERRED", confidence=0.55
    )
    assert hq.evidence.resolve_for_song(song_id=song_a.id, key="chorus.energy").status == "CONFLICT"
    reconciled = hq.evidence.reconcile_for_song(
        song_id=song_a.id, key="chorus.energy", value="needs lift", source_kind="USER_DECLARED", source_ref="artist-confirmation"
    )
    hq.close()

    hq = HeadquartersMemory.open(td, profile_id)
    changed = hq.activity.for_song(song_a.id, after_sequence=checkpoint)
    other = hq.activity.for_song(song_b.id, after_sequence=checkpoint)
    restored = hq.store.get_song(song_a.id)
    assert restored.current_version_id == v2.id
    assert restored.approved_version_id == v1.id
    assert [event.sequence for event in changed] == sorted(event.sequence for event in changed)
    assert any(event.event_type == "EVIDENCE_CLAIM_RECORDED" and event.object_id == reconciled.id for event in changed)
    assert len([event for event in changed if event.event_type == "EVIDENCE_SUPERSESSION_LINKED" and event.object_id == reconciled.id]) == 2
    assert all(event.song_id == song_a.id for event in changed)
    assert all(event.song_id == song_b.id for event in other)
    assert hq.evidence.get_claim(first.id) is not None and hq.evidence.get_claim(second.id) is not None
    hq.close()

print("CORE-01C CONSUMER SMOKE: GREEN: Song Activity survives restart, stays scoped, and preserves approval and evidence history")
