#!/usr/bin/env python3
"""Stage-aware construction smoke.

Pre-product stages prove that product code is absent. CORE-01A proves a tiny
normal consumer outcome: create a Song, preserve current/approved version truth,
close, reopen, and recover the same identities.
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

if active != "CORE-01" or state.get("active_increment") != "CORE-01A":
    print(f"STAGE SMOKE: RED: unsupported active stage {active}/{state.get('active_increment')}", file=sys.stderr)
    raise SystemExit(1)

from n0te2 import LineageStore  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    store = LineageStore.create(td, "Smoke Artist")
    profile_id = store.profile_id
    artist_id = store.primary_artist_id
    song = store.create_song("Smoke Song")
    asset = store.attach_asset(song.id, name="source.wav", sha256="f" * 64)
    v1 = store.create_version(song.id, label="v1", asset_ids=[asset.id])
    store.approve_version(song.id, v1.id)
    v2 = store.create_version(
        song.id,
        label="v2",
        parent_version_id=v1.id,
        asset_ids=[asset.id],
    )
    store.close()

    reopened = LineageStore.open(td, profile_id)
    restored = reopened.get_song(song.id)
    assert reopened.primary_artist_id == artist_id
    assert reopened.active_song().id == song.id
    assert restored.current_version_id == v2.id
    assert restored.approved_version_id == v1.id
    assert reopened.get_version(v2.id).parent_version_id == v1.id
    reopened.close()

print("CORE-01A CONSUMER SMOKE: GREEN: durable Song identity and current/approved lineage survive restart")
