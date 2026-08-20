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

if active != "CORE-02" or increment != "CORE-02B":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import HeadquartersMemory, ValidationError  # noqa: E402


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    hq = HeadquartersMemory.create(root, "Skill Smoke Artist")
    profile = hq.store.profile_id
    song = hq.store.create_song("Skill Smoke Song")
    version = hq.store.create_version(song.id, label="v1")
    skill_id = "production.compression.intentional_dynamics"

    # Rough Session thinking must not teach N0TE a skill level by itself.
    assert hq.skills.state(skill_id).level == "UNKNOWN"
    first = hq.sessions.start_session(
        song_id=song.id,
        version_id=version.id,
        objective="Practice compression while preserving transient intent",
    )
    hq.sessions.append_scratch(
        first.id,
        kind="OBSERVATION",
        body="I think slower attack keeps the snare alive",
    )
    hq.sessions.append_scratch(
        first.id,
        kind="UNRESOLVED",
        body="Still unsure when release should follow groove versus envelope",
    )
    assert hq.skills.state(skill_id).level == "UNKNOWN"
    hq.sessions.close_session(
        first.id,
        debrief_summary="Applied compression deliberately but still needed guidance",
        next_action="Repeat on a different source with less assistance",
    )

    practiced = hq.skills.record_assessment(
        skill_id=skill_id,
        level="PRACTICED",
        source_kind="N0TE_ASSESSED",
        source_ref="smoke:assessment:practice",
        confidence=0.80,
        assistance_level=0.50,
        session_id=first.id,
        note="Completed a real Song task with substantial guidance",
    )
    assert practiced.level == "PRACTICED"
    assert hq.skills.state(skill_id).level == "PRACTICED"

    second = hq.sessions.start_session(
        song_id=song.id,
        version_id=version.id,
        objective="Apply compression independently on the same Song",
    )
    hq.sessions.append_scratch(
        second.id,
        kind="DECISION",
        body="Set attack from transient intent, then tune release by groove",
    )
    hq.sessions.close_session(
        second.id,
        debrief_summary="Completed the compression decision without assistance",
        next_action="Check whether the skill transfers to unfamiliar material",
    )

    try:
        hq.skills.record_assessment(
            skill_id=skill_id,
            level="INDEPENDENT",
            source_kind="OBSERVED",
            source_ref="smoke:bad-independent",
            confidence=0.90,
            assistance_level=0.10,
            session_id=second.id,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("INDEPENDENT must require zero assistance")

    independent = hq.skills.record_assessment(
        skill_id=skill_id,
        level="INDEPENDENT",
        source_kind="OBSERVED",
        source_ref="smoke:assessment:independent",
        confidence=0.90,
        assistance_level=0.0,
        session_id=second.id,
        note="Completed the represented Song task with no assistance",
    )
    assert independent.level == "INDEPENDENT"

    corrected = hq.skills.correct_skill(
        skill_id=skill_id,
        level="PRACTICED",
        source_ref="smoke:artist-correction",
        reason="I can do this here, but I still need help transferring it to unfamiliar material",
        confidence=1.0,
        assistance_level=0.35,
        session_id=second.id,
    )
    assert corrected.source_kind == "ARTIST_CORRECTION"
    assert hq.skills.state(skill_id).level == "PRACTICED"

    history_ids = tuple(item.id for item in hq.skills.history(skill_id))
    assert len(history_ids) == 3
    hq.close()

    hq = HeadquartersMemory.open(root, profile)
    state_after_restart = hq.skills.state(skill_id)
    assert state_after_restart.level == "PRACTICED"
    history = hq.skills.history(skill_id)
    assert tuple(item.id for item in history) == history_ids
    assert tuple(item.level for item in history) == (
        "PRACTICED",
        "INDEPENDENT",
        "PRACTICED",
    )
    assert history[-1].source_kind == "ARTIST_CORRECTION"
    assert history[-1].note.startswith("I can do this here")

    event_types = [event.event_type for event in hq.activity.for_song(song.id)]
    assert event_types.count("SKILL_ASSESSED") == 3

    before_changes = hq.store._conn.total_changes
    assert hq.skills.state(skill_id).level == "PRACTICED"
    assert hq.skills.history(skill_id) == history
    assert hq.store._conn.total_changes == before_changes
    hq.close()

print(
    "CORE-02B CONSUMER SMOKE: GREEN: Session scratch did not infer competence; explicit closed-session assessments matured skill evidence, zero-assistance independence was enforced, and artist correction remained durable/reviewable after restart"
)
