from __future__ import annotations

from pathlib import Path

from n0te2 import HeadquartersMemory
from n0te2.success_patterns import SongSuccessPatterns


def add_episode(
    hq: HeadquartersMemory,
    song_id: str,
    *,
    subject: str,
    change: str,
    decision: str | None,
    observation: str = "Observed result",
    source_kind: str = "USER_DECLARED",
    confidence: float = 0.8,
    conditions: tuple[str, ...] = (),
    confounders: tuple[str, ...] = (),
):
    session = hq.sessions.start_session(song_id=song_id, objective=f"Test {subject}")
    episode = hq.learning.create_episode(
        session_id=session.id,
        domain="Mixing",
        subject_ref=subject,
        change_description=change,
    )
    if observation:
        hq.learning.append_consequence(
            episode.id,
            observation=observation,
            source_kind=source_kind,
            source_ref=f"test:{subject}:{session.id}",
            confidence=confidence,
            conditions=conditions,
            confounders=confounders,
        )
    if decision is not None:
        hq.learning.decide(
            episode.id,
            decision=decision,
            rationale=f"Decision for {subject}",
            confidence=confidence,
        )
    return episode


def test_projection_preserves_all_humility_states_and_strips_internal_identity(tmp_path: Path) -> None:
    hq = HeadquartersMemory.create(tmp_path / "data", "Pattern Artist")
    try:
        song = hq.store.create_song("Pattern Song")
        # Pending only -> NO_COMPLETED_EVIDENCE.
        pending = add_episode(
            hq,
            song.id,
            subject="Pending",
            change="Try pending move",
            decision=None,
            observation="Early observation",
        )
        # One KEEP -> SINGLE_OBSERVATION.
        single = add_episode(
            hq,
            song.id,
            subject="Single",
            change="Try single move",
            decision="KEEP",
            observation="Single retained result",
            source_kind="MEASURED",
            conditions=("matched level",),
            confounders=("different listening position",),
        )
        # Two KEEP -> SUCCESS_ONLY.
        add_episode(hq, song.id, subject="Success", change="Try repeated move", decision="KEEP")
        add_episode(hq, song.id, subject="Success", change="Try repeated move", decision="KEEP")
        # KEEP + REVERT -> MIXED.
        add_episode(hq, song.id, subject="Mixed", change="Try mixed move", decision="KEEP")
        add_episode(hq, song.id, subject="Mixed", change="Try mixed move", decision="REVERT")
        # REVISE only -> NO_KEEP_EVIDENCE.
        add_episode(hq, song.id, subject="No keep", change="Try weak move", decision="REVISE")
        # INCONCLUSIVE only -> INCONCLUSIVE_ONLY.
        add_episode(hq, song.id, subject="Unclear", change="Try unclear move", decision="INCONCLUSIVE")

        views = SongSuccessPatterns(hq.store, hq.success).for_song(song.id)
        by_subject = {item.subject: item for item in views}
        assert by_subject["Pending"].humility_state == "NO_COMPLETED_EVIDENCE"
        assert by_subject["Single"].humility_state == "SINGLE_OBSERVATION"
        assert by_subject["Success"].humility_state == "SUCCESS_ONLY"
        assert by_subject["Mixed"].humility_state == "MIXED"
        assert by_subject["No keep"].humility_state == "NO_KEEP_EVIDENCE"
        assert by_subject["Unclear"].humility_state == "INCONCLUSIVE_ONLY"

        single_view = by_subject["Single"]
        assert single_view.completed_count == 1
        assert single_view.pending_count == 0
        assert single_view.keep_count == 1
        assert single_view.observations[0].source_labels == ("measured",)
        assert single_view.observations[0].confidence_mean == 0.8
        assert single_view.conditions[0].term == "matched level"
        assert single_view.alternative_explanations[0].term == "different listening position"
        assert "not proof" in single_view.warning.lower()

        # The artist-facing projection has no internal pattern/episode/source-ref fields.
        serialized = repr(views)
        assert pending.id not in serialized
        assert single.id not in serialized
        assert "success_" not in serialized
        assert "test:Single:" not in serialized
    finally:
        hq.close()


def test_projection_is_song_scoped_and_pure_read(tmp_path: Path) -> None:
    hq = HeadquartersMemory.create(tmp_path / "data", "Pattern Artist")
    try:
        song_a = hq.store.create_song("A")
        song_b = hq.store.create_song("B")
        add_episode(hq, song_a.id, subject="A-only", change="Move A", decision="KEEP")
        add_episode(hq, song_b.id, subject="B-only", change="Move B", decision="REVERT")

        before_a = hq.learning.episodes_for_song(song_a.id)
        before_b = hq.learning.episodes_for_song(song_b.id)
        a_views = SongSuccessPatterns(hq.store, hq.success).for_song(song_a.id)
        b_views = SongSuccessPatterns(hq.store, hq.success).for_song(song_b.id)
        assert [item.subject for item in a_views] == ["A-only"]
        assert [item.subject for item in b_views] == ["B-only"]
        assert hq.learning.episodes_for_song(song_a.id) == before_a
        assert hq.learning.episodes_for_song(song_b.id) == before_b
    finally:
        hq.close()
