#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "CORE-02" or state.get("active_increment") != "CORE-02E":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.memory import HeadquartersMemory  # noqa: E402


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    hq = HeadquartersMemory.create(root, "Artist")
    profile_id = hq.store.profile_id
    song = hq.store.create_song("Success Memory Song")
    version = hq.store.create_version(song.id, label="v1")

    def episode(decision, source_ref, confidence, *, confounders=()):
        session = hq.sessions.start_session(
            song_id=song.id,
            version_id=version.id,
            objective="Evaluate a bounded vocal EQ change",
        )
        item = hq.learning.create_episode(
            session_id=session.id,
            domain="MIX",
            subject_ref="vocal",
            change_description="Cut 300 Hz",
        )
        hq.learning.append_consequence(
            item.id,
            observation="The vocal feels clearer",
            source_kind="OBSERVED",
            source_ref=source_ref,
            confidence=confidence,
            conditions=("same chorus", "same monitor level"),
            confounders=confounders,
        )
        hq.learning.decide(
            item.id,
            decision=decision,
            rationale=f"{decision} after bounded comparison",
            confidence=confidence,
        )
        return item

    first = episode("KEEP", "listen:1", 0.8, confounders=("fresh ears",))
    second = episode("KEEP", "listen:2", 0.6, confounders=("listening order",))

    before = hq.store._conn.total_changes
    (promising,) = hq.success.patterns_for_song(song.id)
    assert hq.store._conn.total_changes == before
    assert promising.humility_state == "SUCCESS_ONLY"
    assert promising.causal_status == "ASSOCIATION_ONLY"
    assert promising.sample_size == 2
    assert promising.supporting_episode_ids == (first.id, second.id)
    assert promising.counterexample_episode_ids == ()
    assert "Absence of counterexamples" in promising.warning
    assert {item.term for item in promising.alternative_explanations} == {
        "fresh ears",
        "listening order",
    }
    assert promising.consequence_confidence.minimum == 0.6
    assert promising.consequence_confidence.maximum == 0.8

    third = episode("REVERT", "listen:3", 0.9, confounders=("different vocal take",))
    (mixed,) = hq.success.patterns_for_song(song.id)
    assert mixed.humility_state == "MIXED"
    assert mixed.supporting_episode_ids == (first.id, second.id)
    assert mixed.counterexample_episode_ids == (third.id,)
    assert mixed.has_counterexamples
    assert mixed.causal_status == "ASSOCIATION_ONLY"

    # Success Memory is a lens, not another persistence owner.
    assert hq.store._conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'success%'"
    ).fetchone()[0] == 0
    snapshot = hq.success.patterns_for_song(song.id)
    hq.close()

    hq = HeadquartersMemory.open(root, profile_id)
    assert hq.success.patterns_for_song(song.id) == snapshot
    hq.close()

print(
    "CORE-02E CONSUMER SMOKE: GREEN: two retained examples were remembered with conditions, confounders, confidence and an explicit survivorship warning rather than causal certainty; a later REVERT became visible counterevidence and changed the pattern to MIXED; Success Memory created no persistence table and restart reproduced the same synthesis"
)
