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

if active != "CORE-02" or increment != "CORE-02D":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import HeadquartersMemory  # noqa: E402


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    hq = HeadquartersMemory.create(root, "Friction Smoke Artist")
    profile = hq.store.profile_id
    song = hq.store.create_song("Friction Smoke Song")
    version = hq.store.create_version(song.id, label="v1")

    evidence_before = hq.store._conn.execute(
        "SELECT COUNT(*) FROM evidence_claims"
    ).fetchone()[0]
    skill_before = hq.store._conn.execute(
        "SELECT COUNT(*) FROM skill_assessments"
    ).fetchone()[0]

    first_session = hq.sessions.start_session(
        song_id=song.id,
        version_id=version.id,
        objective="Track the verse without diagnosing one distraction as a pattern",
    )
    first_episode = hq.learning.create_episode(
        session_id=first_session.id,
        domain="PROCESS",
        subject_ref="tracking.focus",
        change_description="Observe the tracking workflow",
    )
    first = hq.friction.record(
        episode_id=first_episode.id,
        friction_key="context-switching",
        description="Checking notifications interrupted the vocal take",
        source_kind="USER_DECLARED",
        source_ref="smoke:first-session",
        confidence=0.90,
        prevention_hint="Silence notifications before tracking",
    )
    assert hq.friction.recurring_patterns() == ()
    hq.sessions.close_session(
        first_session.id,
        debrief_summary="One focus interruption was explicitly recorded",
        next_action="Continue normally; one incident is not a recurring rule",
    )

    second_session = hq.sessions.start_session(
        song_id=song.id,
        version_id=version.id,
        objective="Track the chorus and notice whether the explicit blocker recurs",
    )
    second_episode = hq.learning.create_episode(
        session_id=second_session.id,
        domain="PROCESS",
        subject_ref="tracking.focus",
        change_description="Observe the second tracking workflow",
    )
    second = hq.friction.record(
        episode_id=second_episode.id,
        friction_key="context-switching",
        description="Message checking broke focus before the chorus take",
        source_kind="OBSERVED",
        source_ref="smoke:second-session",
        confidence=0.80,
        prevention_hint="Use a dedicated tracking focus mode",
    )
    hq.friction.record(
        episode_id=second_episode.id,
        friction_key="plugin-browsing",
        description="A short plugin search slowed one transition",
        source_kind="OBSERVED",
        source_ref="smoke:single-other-key",
        confidence=0.60,
    )

    patterns = hq.friction.recurring_patterns()
    assert len(patterns) == 1
    pattern = patterns[0]
    assert pattern.friction_key == "context-switching"
    assert pattern.occurrences == (first, second)
    assert pattern.session_count == 2
    assert pattern.session_ids == (first_session.id, second_session.id)
    assert pattern.prevention_hints == (
        "Silence notifications before tracking",
        "Use a dedicated tracking focus mode",
    )
    hq.sessions.close_session(
        second_session.id,
        debrief_summary="The same explicitly named focus blocker recurred in a distinct Session",
        next_action="Review the prior prevention hints before the next tracking Session",
    )

    # Recurrence is process evidence only. It must not silently become doctrine.
    assert hq.store._conn.execute(
        "SELECT COUNT(*) FROM evidence_claims"
    ).fetchone()[0] == evidence_before
    assert hq.store._conn.execute(
        "SELECT COUNT(*) FROM skill_assessments"
    ).fetchone()[0] == skill_before
    assert hq.store._conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name IN ('causal_rules','success_rules','friction_rules')"
    ).fetchone()[0] == 0
    hq.close()

    hq = HeadquartersMemory.open(root, profile)
    restored = hq.friction.recurring_patterns()
    assert len(restored) == 1
    assert restored[0].friction_key == "context-switching"
    assert restored[0].session_count == 2
    assert restored[0].prevention_hints == pattern.prevention_hints
    assert hq.friction.recurring_patterns(song_id=song.id) == restored

    event_types = [event.event_type for event in hq.activity.for_song(song.id)]
    assert event_types.count("FRICTION_OBSERVED") == 3

    before_changes = hq.store._conn.total_changes
    assert hq.friction.recurring_patterns() == restored
    assert hq.friction.observations(friction_key="context-switching") == (
        first,
        second,
    )
    assert hq.store._conn.total_changes == before_changes
    hq.close()

print(
    "CORE-02D CONSUMER SMOKE: GREEN: one explicit workflow incident stayed ordinary history; the same friction key across a second distinct Session became a reviewable recurring pattern with preserved prevention hints and no Skill, Evidence, success or causal doctrine"
)
