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

if active != "CORE-02" or increment != "CORE-02C":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import HeadquartersMemory, ValidationError  # noqa: E402


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    hq = HeadquartersMemory.create(root, "Learning Smoke Artist")
    profile = hq.store.profile_id
    song = hq.store.create_song("Learning Smoke Song")
    version = hq.store.create_version(song.id, label="v1")
    session = hq.sessions.start_session(
        song_id=song.id,
        version_id=version.id,
        objective="Test one compression change without turning one listen into doctrine",
    )

    evidence_before = hq.store._conn.execute(
        "SELECT COUNT(*) FROM evidence_claims"
    ).fetchone()[0]
    skill_before = hq.store._conn.execute(
        "SELECT COUNT(*) FROM skill_assessments"
    ).fetchone()[0]

    episode = hq.learning.create_episode(
        session_id=session.id,
        domain="MIX",
        subject_ref="chorus.vocal.compression",
        change_description="Lower compressor ratio from 6:1 to 3:1",
    )
    first = hq.learning.append_consequence(
        episode.id,
        observation="The chorus vocal transient feels more alive",
        source_kind="OBSERVED",
        source_ref="smoke:artist-listen",
        confidence=0.70,
        conditions=("same chorus section", "level matched"),
        confounders=("fresh ears", "single listening session"),
    )
    second = hq.learning.append_consequence(
        episode.id,
        observation="Peak level increased by about 1 dB",
        source_kind="MEASURED",
        source_ref="smoke:meter",
        confidence=0.95,
        conditions=("same render path",),
        confounders=("compressor makeup gain may differ",),
    )
    assert first.conditions == ("same chorus section", "level matched")
    assert second.confounders == ("compressor makeup gain may differ",)

    # Positive observations are evidence of what followed, not a causal/success rule.
    assert hq.store._conn.execute(
        "SELECT COUNT(*) FROM evidence_claims"
    ).fetchone()[0] == evidence_before
    assert hq.store._conn.execute(
        "SELECT COUNT(*) FROM skill_assessments"
    ).fetchone()[0] == skill_before
    assert hq.skills.state("skill:compression").level == "UNKNOWN"
    assert hq.store._conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name IN ('causal_rules','success_rules','friction_rules')"
    ).fetchone()[0] == 0

    decision = hq.learning.decide(
        episode.id,
        decision="INCONCLUSIVE",
        rationale=(
            "The transient improvement is promising, but the makeup-gain difference and "
            "single listening session mean the change should be re-tested before generalizing"
        ),
        confidence=0.60,
    )
    assert decision.decision == "INCONCLUSIVE"

    try:
        hq.learning.append_consequence(
            episode.id,
            observation="Try to append after decision",
            source_kind="OBSERVED",
            source_ref="smoke:late",
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("Learning consequences must stop after explicit decision")

    hq.sessions.close_session(
        session.id,
        debrief_summary="One compression change was tested and kept explicitly inconclusive",
        next_action="Repeat the comparison tomorrow with matched makeup gain",
    )
    hq.close()

    hq = HeadquartersMemory.open(root, profile)
    restored = hq.learning.get_episode(episode.id)
    assert restored is not None
    assert restored.change_description == "Lower compressor ratio from 6:1 to 3:1"
    assert tuple(item.id for item in restored.consequences) == (first.id, second.id)
    assert restored.decision is not None
    assert restored.decision.id == decision.id
    assert restored.decision.decision == "INCONCLUSIVE"
    assert "re-tested before generalizing" in restored.decision.rationale

    event_types = [event.event_type for event in hq.activity.for_song(song.id)]
    for required in (
        "LEARNING_EPISODE_STARTED",
        "LEARNING_CONSEQUENCE_RECORDED",
        "LEARNING_DECISION_RECORDED",
    ):
        assert required in event_types

    before_changes = hq.store._conn.total_changes
    assert hq.learning.get_episode(episode.id) == restored
    assert hq.learning.episodes_for_song(song.id) == (restored,)
    assert hq.store._conn.total_changes == before_changes
    hq.close()

print(
    "CORE-02C CONSUMER SMOKE: GREEN: a real Song change preserved observed consequences, conditions/confounders and an explicit inconclusive decision after restart without auto-creating Skill, Evidence, success, friction or causal doctrine"
)
