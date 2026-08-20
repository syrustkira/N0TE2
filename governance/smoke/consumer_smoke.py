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

if active != "CORE-01" or increment != "CORE-01D":
    print(f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}", file=sys.stderr)
    raise SystemExit(1)

from n0te2 import HeadquartersMemory, SongResumeService  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    hq = HeadquartersMemory.create(td, "Smoke Artist")
    profile = hq.store.profile_id
    song_a = hq.store.create_song("Smoke Song A")
    song_b = hq.store.create_song("Smoke Song B")
    asset = hq.store.attach_asset(song_a.id, name="source.wav", sha256="f" * 64)
    v1 = hq.store.create_version(song_a.id, label="v1", asset_ids=[asset.id])
    hq.store.approve_version(song_a.id, v1.id)
    v2 = hq.store.create_version(song_a.id, label="v2", parent_version_id=v1.id, asset_ids=[asset.id])
    hq.evidence.record_claim(
        scope_kind="ARTIST", scope_id=hq.store.primary_artist_id, key="next.action", value="review arrangement", source_kind="REMEMBERED"
    )
    hq.evidence.record_claim(
        scope_kind="SONG", scope_id=song_a.id, key="next.action", value="record bridge", source_kind="USER_DECLARED"
    )
    next_claim = hq.evidence.record_claim(
        scope_kind="VERSION", scope_id=v2.id, key="next.action", value="tighten chorus", source_kind="USER_DECLARED", source_ref="session-end"
    )
    first = hq.evidence.record_claim(
        scope_kind="SONG", scope_id=song_a.id, key="chorus.energy", value="needs lift", source_kind="USER_DECLARED"
    )
    second = hq.evidence.record_claim(
        scope_kind="SONG", scope_id=song_a.id, key="chorus.energy", value="already right", source_kind="INFERRED", confidence=0.55
    )
    hq.evidence.record_claim(
        scope_kind="SONG", scope_id=song_b.id, key="chorus.energy", value="other song only", source_kind="USER_DECLARED"
    )
    hq.store.select_song(song_a.id)
    hq.close()

    hq = HeadquartersMemory.open(td, profile)
    conn = hq.store._conn
    tables = ("metadata", "songs", "versions", "assets", "evidence_claims", "evidence_supersessions", "activity_events")
    before = {table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]) for table in tables}
    brief = SongResumeService(hq).brief(recent_limit=50)
    after = {table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]) for table in tables}

    assert before == after
    assert brief.song_id == song_a.id and brief.song_title == "Smoke Song A" and brief.is_active_song
    assert brief.current_version.id == v2.id and brief.approved_version.id == v1.id
    assert brief.next_action_status == "RESOLVED" and brief.next_action == "tighten chorus"
    assert brief.next_action_evidence[0].claim_id == next_claim.id
    conflict = next(c for c in brief.unresolved_conflicts if c.key == "chorus.energy")
    assert {e.claim_id for e in conflict.evidence} == {first.id, second.id}
    assert all(change.sequence > 0 for change in brief.recent_changes)
    assert [change.sequence for change in brief.recent_changes] == sorted(change.sequence for change in brief.recent_changes)
    assert song_b.id not in {change.object_id for change in brief.recent_changes}
    hq.close()

print("CORE-01D CONSUMER SMOKE: GREEN: returning artist gets a pure-read Song Resume Brief with current/approved, changes, conflicts and represented next-action truth")
