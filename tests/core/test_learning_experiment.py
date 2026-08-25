from __future__ import annotations

from pathlib import Path

import pytest

from n0te2 import HeadquartersMemory
from n0te2.learning_experiment import (
    LearningExperimentService,
    StaleLearningExperimentError,
)


def setup_work(tmp_path: Path):
    hq = HeadquartersMemory.create(tmp_path / "data", "Learning Artist")
    song = hq.store.create_song("Learning Song")
    session = hq.sessions.start_session(song_id=song.id, objective="Try one deliberate mix change")
    return hq, song, session


def test_change_observation_decision_uses_canonical_learning_memory(tmp_path: Path) -> None:
    hq, song, session = setup_work(tmp_path)
    try:
        service = LearningExperimentService(hq.learning)
        binding = service.start_binding()
        assert binding is not None
        assert binding.song_id == song.id
        assert binding.session_id == session.id

        episode = service.start_episode(
            binding,
            domain="Mixing",
            subject="Vocal compression",
            change_description="Lengthened the compressor attack so the vocal transient could pass.",
        )
        assert episode.song_id == song.id
        assert episode.session_id == session.id
        assert episode.consequences == ()
        assert episode.decision is None

        observation = service.append_observation(
            episode.id,
            observation="The consonants felt clearer while the level stayed controlled.",
            confidence="MEDIUM",
            conditions="Same vocal take and matched monitor level",
            confounders="I also listened after a short ear break",
        )
        assert observation.source_kind == "USER_DECLARED"
        assert observation.source_ref.startswith("consumer-learning-observation:")
        assert observation.confidence == 0.7
        assert observation.conditions == ("Same vocal take and matched monitor level",)
        assert observation.confounders == ("I also listened after a short ear break",)

        decision_binding = service.decision_binding(episode.id)
        decision = service.decide(
            decision_binding,
            decision="KEEP",
            rationale="Keep the slower attack for this vocal and compare again later in the full mix.",
            confidence="MEDIUM",
        )
        assert decision.decision == "KEEP"
        assert decision.confidence == 0.7

        stored = hq.learning.get_episode(episode.id)
        assert stored is not None
        assert len(stored.consequences) == 1
        assert stored.decision == decision

        kinds = [item.event_type for item in hq.activity.for_song(song.id)]
        assert "LEARNING_EPISODE_STARTED" in kinds
        assert "LEARNING_CONSEQUENCE_RECORDED" in kinds
        assert "LEARNING_DECISION_RECORDED" in kinds
    finally:
        hq.close()


def test_start_binding_fails_closed_when_rendered_session_closes(tmp_path: Path) -> None:
    hq, _, session = setup_work(tmp_path)
    try:
        service = LearningExperimentService(hq.learning)
        binding = service.start_binding()
        assert binding is not None
        hq.sessions.close_session(
            session.id,
            debrief_summary="Stopped before running the experiment",
            next_action="Start a fresh work Session",
        )
        with pytest.raises(StaleLearningExperimentError, match="Session changed"):
            service.start_episode(
                binding,
                domain="Arrangement",
                subject="Chorus density",
                change_description="Remove one supporting layer.",
            )
        assert hq.learning.episodes_for_song(binding.song_id) == ()
    finally:
        hq.close()


def test_decision_rejects_unseen_new_observation_atomically(tmp_path: Path) -> None:
    hq, _, _ = setup_work(tmp_path)
    try:
        service = LearningExperimentService(hq.learning)
        start = service.start_binding()
        assert start is not None
        episode = service.start_episode(
            start,
            domain="Arrangement",
            subject="Pre-chorus lift",
            change_description="Muted the bass for the final half-bar before the chorus.",
        )
        service.append_observation(
            episode.id,
            observation="The chorus entrance felt larger.",
            confidence="MEDIUM",
        )
        stale = service.decision_binding(episode.id)
        service.append_observation(
            episode.id,
            observation="The pre-chorus also felt slightly emptier than intended.",
            confidence="HIGH",
            confounders="The chorus synth was louder than the reference balance",
        )

        with pytest.raises(StaleLearningExperimentError, match="New Learning evidence"):
            service.decide(
                stale,
                decision="KEEP",
                rationale="The first observation looked positive.",
                confidence="HIGH",
            )
        stored = hq.learning.get_episode(episode.id)
        assert stored is not None
        assert len(stored.consequences) == 2
        assert stored.decision is None
    finally:
        hq.close()


def test_terminal_decision_closes_episode_but_not_session(tmp_path: Path) -> None:
    hq, song, session = setup_work(tmp_path)
    try:
        service = LearningExperimentService(hq.learning)
        start = service.start_binding()
        assert start is not None
        episode = service.start_episode(
            start,
            domain="Sound design",
            subject="Pad width",
            change_description="Reduced the chorus width before the vocal entered.",
        )
        service.append_observation(
            episode.id,
            observation="The vocal center felt easier to locate.",
            confidence="LOW",
        )
        service.decide(
            service.decision_binding(episode.id),
            decision="REVISE",
            rationale="Try a smaller width reduction before keeping the move.",
            confidence="MEDIUM",
        )

        with pytest.raises(StaleLearningExperimentError, match="final decision"):
            service.append_observation(
                episode.id,
                observation="Another observation after closure",
                confidence="MEDIUM",
            )
        assert hq.sessions.get_session(session.id).state == "OPEN"
        assert hq.store.active_song().id == song.id
    finally:
        hq.close()


def test_decision_remains_possible_after_session_close_and_reopen(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    hq = HeadquartersMemory.create(data_root, "Learning Artist")
    profile_id = hq.store.profile_id
    song = hq.store.create_song("Learning Song")
    session = hq.sessions.start_session(song_id=song.id, objective="Try one move")
    service = LearningExperimentService(hq.learning)
    start = service.start_binding()
    assert start is not None
    episode = service.start_episode(
        start,
        domain="Mixing",
        subject="Reverb pre-delay",
        change_description="Increased vocal reverb pre-delay.",
    )
    service.append_observation(
        episode.id,
        observation="The vocal felt more forward while the tail stayed audible.",
        confidence="MEDIUM",
    )
    hq.sessions.close_session(
        session.id,
        debrief_summary="Captured the result and stopped",
        next_action="Review the Learning observation",
    )
    hq.close()

    reopened = HeadquartersMemory.open(data_root, profile_id)
    try:
        service = LearningExperimentService(reopened.learning)
        assert service.start_binding() is None
        stored = reopened.learning.get_episode(episode.id)
        assert stored is not None and stored.decision is None
        decision = service.decide(
            service.decision_binding(episode.id),
            decision="INCONCLUSIVE",
            rationale="I need another matched comparison before adopting the move.",
            confidence="LOW",
        )
        assert decision.decision == "INCONCLUSIVE"
        assert reopened.sessions.get_session(session.id).state == "CLOSED"
    finally:
        reopened.close()
