#!/usr/bin/env python3
"""Stage-aware construction smoke for the active bounded consumer outcome."""
from __future__ import annotations

import tempfile
from pathlib import Path
import sys

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))

import json  # noqa: E402

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

if active != "CORE-01" or increment != "CORE-01F":
    print(f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}", file=sys.stderr)
    raise SystemExit(1)

from n0te2 import HeadquartersMemory, LineageCorruptionError, RecoveryManager, SnapshotHashMismatchError  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    hq = HeadquartersMemory.create(td, "Smoke Artist")
    profile = hq.store.profile_id
    song = hq.store.create_song("Smoke Song")
    v1 = hq.store.create_version(song.id, label="v1")
    hq.store.approve_version(song.id, v1.id)
    snapshot = hq.recovery.create_snapshot()
    v2 = hq.store.create_version(song.id, label="v2", parent_version_id=v1.id)
    hq.evidence.record_claim(
        scope_kind="SONG", scope_id=song.id, key="post.snapshot", value="later state", source_kind="OBSERVED"
    )
    live = hq.store.database_path
    hq.close()

    live.write_bytes(b"deliberately corrupt live state")
    try:
        HeadquartersMemory.open(td, profile)
    except LineageCorruptionError:
        pass
    else:
        raise AssertionError("normal open silently recovered corrupt live state")

    inspected = RecoveryManager.inspect_snapshot(td, profile)
    assert inspected.sha256 == snapshot.sha256
    try:
        RecoveryManager.restore_snapshot(td, profile, expected_sha256="0" * 64)
    except SnapshotHashMismatchError:
        pass
    else:
        raise AssertionError("wrong restore hash was accepted")
    assert live.read_bytes() == b"deliberately corrupt live state"

    restored = RecoveryManager.restore_snapshot(td, profile, expected_sha256=snapshot.sha256)
    assert restored.preserved_database is not None and restored.preserved_database.is_file()
    assert restored.preserved_database.read_bytes() == b"deliberately corrupt live state"

    hq = HeadquartersMemory.open(td, profile)
    recovered = hq.store.get_song(song.id)
    assert recovered.current_version_id == v1.id
    assert recovered.approved_version_id == v1.id
    assert hq.store.get_version(v2.id) is None
    assert hq.evidence.resolve_for_song(song_id=song.id, key="post.snapshot").status == "UNKNOWN"
    hq.close()

print("CORE-01F CONSUMER SMOKE: GREEN: corrupt live memory fails visibly and restores only through explicit verified local snapshot authority")
