#!/usr/bin/env python3
"""Stage-aware construction smoke for the active bounded consumer outcome."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

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
                f"PRE-PRODUCT SMOKE: RED: product implementation appeared early: {forbidden}/",
                file=sys.stderr,
            )
            raise SystemExit(1)
    print("PRE-PRODUCT SMOKE: GREEN")
    raise SystemExit(0)

if active != "CORE-02" or increment != "CORE-02A":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import HeadquartersMemory  # noqa: E402


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    hq = HeadquartersMemory.create(root, "Session Smoke Artist")
    profile = hq.store.profile_id
    song = hq.store.create_song("Session Smoke Song")
    version = hq.store.create_version(song.id, label="v1")

    session = hq.sessions.start_session(
        song_id=song.id,
        version_id=version.id,
        objective="Find the chorus emotional shape without polishing the mix",
    )
    rejected = hq.sessions.append_scratch(
        session.id,
        kind="REJECTED_IDEA",
        body="Double every chorus guitar",
    )
    unresolved = hq.sessions.append_scratch(
        session.id,
        kind="UNRESOLVED",
        body="Maybe the lead vocal needs more space",
    )
    decision = hq.sessions.append_scratch(
        session.id,
        kind="DECISION",
        body="Keep the chorus small and tense",
    )

    assert hq.evidence.resolve_for_song(
        song_id=song.id, key="chorus.intent"
    ).status == "UNKNOWN"
    promoted = hq.sessions.promote_item(
        decision.id,
        scope_kind="SONG",
        key="chorus.intent",
        source_kind="USER_DECLARED",
        twin_domain="CREATIVE",
    )
    assert hq.sessions.promote_item(
        decision.id,
        scope_kind="SONG",
        key="chorus.intent",
        source_kind="USER_DECLARED",
        twin_domain="CREATIVE",
    ).id == promoted.id

    closed = hq.sessions.close_session(
        session.id,
        debrief_summary="The smaller chorus keeps the song emotionally tense",
        next_action="Try a lower harmony before changing the mix",
    )
    assert closed.state == "CLOSED"
    hq.close()

    hq = HeadquartersMemory.open(root, profile)
    latest = hq.sessions.latest_for_song(song.id)
    assert latest is not None and latest.id == session.id
    assert latest.state == "CLOSED"
    assert latest.next_action == "Try a lower harmony before changing the mix"
    items = hq.sessions.items_for_session(session.id)
    assert [item.id for item in items] == [rejected.id, unresolved.id, decision.id]

    resolved = hq.evidence.resolve_for_song(song_id=song.id, key="chorus.intent")
    assert resolved.status == "RESOLVED"
    assert resolved.value == "Keep the chorus small and tense"
    assert resolved.claim_ids == (promoted.id,)
    assert hq.sessions.promotion_for_item(rejected.id) is None
    assert hq.sessions.promotion_for_item(unresolved.id) is None

    event_types = [event.event_type for event in hq.activity.for_song(song.id)]
    for required in (
        "SESSION_STARTED",
        "SESSION_SCRATCH_ADDED",
        "SESSION_ITEM_PROMOTED",
        "SESSION_CLOSED",
    ):
        assert required in event_types

    before_changes = hq.store._conn.total_changes
    assert hq.sessions.latest_for_song(song.id).id == session.id
    assert hq.sessions.items_for_session(session.id) == items
    assert hq.sessions.promotion_for_item(decision.id).claim_id == promoted.id
    assert hq.store._conn.total_changes == before_changes
    hq.close()

print(
    "CORE-02A CONSUMER SMOKE: GREEN: Song Session objective/scratch survived restart, only explicit promotion became evidence, and debrief/next action resumed intact"
)
