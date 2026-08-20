#!/usr/bin/env python3
"""Stage-aware construction smoke.

Pre-product stages prove product code is absent. CORE product increments prove
one bounded consumer outcome rather than pretending the whole product is done.
"""
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
            print(
                f"PRE-PRODUCT SMOKE: RED: product implementation appeared early "
                f"(including direct legacy): {forbidden}/",
                file=sys.stderr,
            )
            raise SystemExit(1)
    print("PRE-PRODUCT SMOKE: GREEN: governance/migration-evidence-only repository surface")
    raise SystemExit(0)

if active != "CORE-01" or increment != "CORE-01B":
    print(f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}", file=sys.stderr)
    raise SystemExit(1)

from n0te2 import EvidenceMemory, LineageStore  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    store = LineageStore.create(td, "Smoke Artist")
    profile_id = store.profile_id
    artist_id = store.primary_artist_id
    song_a = store.create_song("Smoke Song A")
    song_b = store.create_song("Smoke Song B")
    asset = store.attach_asset(song_a.id, name="source.wav", sha256="f" * 64)
    v1 = store.create_version(song_a.id, label="v1", asset_ids=[asset.id])
    store.approve_version(song_a.id, v1.id)
    v2 = store.create_version(
        song_a.id,
        label="v2",
        parent_version_id=v1.id,
        asset_ids=[asset.id],
    )

    memory = EvidenceMemory(store)
    memory.record_claim(
        scope_kind="ARTIST",
        scope_id=artist_id,
        key="creative.energy",
        value="restrained",
        source_kind="USER_DECLARED",
    )
    memory.record_claim(
        scope_kind="SONG",
        scope_id=song_a.id,
        key="creative.energy",
        value="explosive",
        source_kind="USER_DECLARED",
    )
    first = memory.record_claim(
        scope_kind="SONG",
        scope_id=song_a.id,
        key="chorus.energy",
        value="needs lift",
        source_kind="USER_DECLARED",
    )
    second = memory.record_claim(
        scope_kind="SONG",
        scope_id=song_a.id,
        key="chorus.energy",
        value="already right",
        source_kind="INFERRED",
        confidence=0.55,
    )
    conflict = memory.resolve_for_song(song_id=song_a.id, key="chorus.energy")
    assert conflict.status == "CONFLICT" and set(conflict.claim_ids) == {first.id, second.id}
    reconciled = memory.reconcile_for_song(
        song_id=song_a.id,
        key="chorus.energy",
        value="needs lift",
        source_kind="USER_DECLARED",
        source_ref="artist-confirmation",
    )
    assert memory.resolve_for_song(song_id=song_a.id, key="creative.energy").value == "explosive"
    assert memory.resolve_for_song(song_id=song_b.id, key="creative.energy").value == "restrained"
    store.close()

    reopened = LineageStore.open(td, profile_id)
    memory = EvidenceMemory(reopened)
    restored = reopened.get_song(song_a.id)
    resolution = memory.resolve_for_song(song_id=song_a.id, key="chorus.energy")
    assert reopened.primary_artist_id == artist_id
    assert restored.current_version_id == v2.id
    assert restored.approved_version_id == v1.id
    assert resolution.status == "RESOLVED" and resolution.value == "needs lift"
    assert resolution.claim_ids == (reconciled.id,)
    assert memory.get_claim(first.id) is not None and memory.get_claim(second.id) is not None
    reopened.close()

print("CORE-01B CONSUMER SMOKE: GREEN: scoped evidence survives restart, respects scope, surfaces conflict, and reconciles without deleting history")
