from __future__ import annotations

import dataclasses
import math
import tempfile
import unittest
from pathlib import Path

from n0te2 import HeadquartersMemory, ValidationError
from n0te2.creative_suggestions import CreativeSuggestion, CreativeSuggestionService
from n0te2.creative_tension import (
    AudienceEvidence,
    AudienceEvidenceView,
    ChallengerExperiment,
    CreativeTensionHypothesis,
    CreativeTensionService,
)
from n0te2.evidence import EvidenceClaim


def canonical_pattern(hq: HeadquartersMemory, song_id: str):
    session = hq.sessions.start_session(
        song_id=song_id,
        objective="Test one creative habit",
    )
    episode = hq.learning.create_episode(
        session_id=session.id,
        domain="SOUND",
        subject_ref="lead texture",
        change_description="use the same bright lead family",
    )
    hq.learning.append_consequence(
        episode.id,
        observation="The lead stayed recognizable.",
        source_kind="OBSERVED",
        source_ref=f"test:{episode.id}",
        confidence=0.75,
        confounders=("single context",),
    )
    hq.learning.decide(
        episode.id,
        decision="KEEP",
        rationale="Retain this experiment.",
        confidence=0.8,
    )
    return hq.success.patterns_for_song(song_id)[0]


def wildcard(
    hq: HeadquartersMemory,
    *,
    provider_used: bool = False,
    authority: bool = False,
) -> CreativeSuggestion:
    canonical = CreativeSuggestionService(hq.store, hq.sessions).suggest(
        distance="WILDCARD",
        locked_dimensions=(),
        variation=0,
    )
    return dataclasses.replace(
        canonical,
        provider_used=provider_used,
        action_authority_granted=authority,
    )


class CreativeTensionBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hq = HeadquartersMemory.create(self.root, "Artist")
        self.addCleanup(self.hq.close)
        self.song = self.hq.store.create_song("Song")
        self.service = CreativeTensionService(
            self.hq.store, self.hq.evidence, self.hq.success
        )

    def test_service_refuses_memory_components_from_another_profile(
        self,
    ) -> None:
        other_root = self.root / "other"
        other = HeadquartersMemory.create(other_root, "Other Artist")
        self.addCleanup(other.close)
        with self.assertRaisesRegex(TypeError, "same LineageStore"):
            CreativeTensionService(
                self.hq.store, other.evidence, self.hq.success
            )
        with self.assertRaisesRegex(TypeError, "same LineageStore"):
            CreativeTensionService(
                self.hq.store, self.hq.evidence, other.success
            )

    def test_audience_evidence_class_and_source_semantics_cannot_cross(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValidationError, "simulated audience source kind"
        ):
            AudienceEvidence(
                song_id=self.song.id,
                version_id=None,
                evidence_class="SIMULATED_FIRST_LISTEN",
                source_kind="LISTENER_SESSION",
                source_ref="listener:1",
                signal="MIXED",
                summary="simulated",
                confidence=0.5,
            )
        with self.assertRaisesRegex(
            ValidationError, "observed audience source kind"
        ):
            AudienceEvidence(
                song_id=self.song.id,
                version_id=None,
                evidence_class="OBSERVED_AUDIENCE",
                source_kind="MODEL_INFERENCE",
                source_ref="model:1",
                signal="MIXED",
                summary="observed",
                confidence=0.5,
                observed_at="2026-09-04T11:00:00Z",
            )

    def test_observed_requires_real_aware_time_and_simulated_cannot_fake_time(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValidationError, "requires observed_at"
        ):
            AudienceEvidence(
                song_id=self.song.id,
                version_id=None,
                evidence_class="OBSERVED_AUDIENCE",
                source_kind="SURVEY",
                source_ref="survey:1",
                signal="NEUTRAL",
                summary="No clear preference.",
                confidence=0.6,
            )
        with self.assertRaisesRegex(
            ValidationError, "ISO-8601 timestamp"
        ):
            AudienceEvidence(
                song_id=self.song.id,
                version_id=None,
                evidence_class="OBSERVED_AUDIENCE",
                source_kind="SURVEY",
                source_ref="survey:2",
                signal="NEUTRAL",
                summary="Malformed time must not count as observation.",
                confidence=0.6,
                observed_at="not-a-time",
            )
        with self.assertRaisesRegex(
            ValidationError, "timezone-aware"
        ):
            AudienceEvidence(
                song_id=self.song.id,
                version_id=None,
                evidence_class="OBSERVED_AUDIENCE",
                source_kind="SURVEY",
                source_ref="survey:3",
                signal="NEUTRAL",
                summary="Naive time must not count as observation.",
                confidence=0.6,
                observed_at="2026-09-04T11:00:00",
            )
        with self.assertRaisesRegex(
            ValidationError, "cannot carry observed_at"
        ):
            AudienceEvidence(
                song_id=self.song.id,
                version_id=None,
                evidence_class="SIMULATED_FIRST_LISTEN",
                source_kind="MODEL_INFERENCE",
                source_ref="model:1",
                signal="NEUTRAL",
                summary="No clear inference.",
                confidence=0.6,
                observed_at="2026-09-04T11:00:00Z",
            )

    def test_boolean_nonfinite_out_of_range_confidence_and_unknown_signal_fail_closed(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValidationError, "confidence must be numeric"
        ):
            AudienceEvidence(
                song_id=self.song.id,
                version_id=None,
                evidence_class="SIMULATED_FIRST_LISTEN",
                source_kind="LOCAL_HEURISTIC",
                source_ref="heuristic:1",
                signal="UNKNOWN",
                summary="No inference.",
                confidence=True,
            )
        for nonfinite in (math.nan, math.inf, -math.inf):
            with self.subTest(nonfinite=nonfinite):
                with self.assertRaisesRegex(
                    ValidationError, "confidence must be finite"
                ):
                    AudienceEvidence(
                        song_id=self.song.id,
                        version_id=None,
                        evidence_class="SIMULATED_FIRST_LISTEN",
                        source_kind="LOCAL_HEURISTIC",
                        source_ref="heuristic:finite",
                        signal="UNKNOWN",
                        summary="No inference.",
                        confidence=nonfinite,
                    )
        with self.assertRaisesRegex(
            ValidationError, "between 0 and 1"
        ):
            AudienceEvidence(
                song_id=self.song.id,
                version_id=None,
                evidence_class="SIMULATED_FIRST_LISTEN",
                source_kind="LOCAL_HEURISTIC",
                source_ref="heuristic:range",
                signal="UNKNOWN",
                summary="No inference.",
                confidence=1.1,
            )
        with self.assertRaisesRegex(
            ValidationError, "unsupported audience signal"
        ):
            AudienceEvidence(
                song_id=self.song.id,
                version_id=None,
                evidence_class="SIMULATED_FIRST_LISTEN",
                source_kind="LOCAL_HEURISTIC",
                source_ref="heuristic:1",
                signal="VIRAL",
                summary="Unsupported certainty.",
                confidence=0.5,
            )

    def test_challenger_rejects_causal_upgrade_and_inconsistent_pattern_counts(
        self,
    ) -> None:
        pattern = canonical_pattern(self.hq, self.song.id)
        causal = dataclasses.replace(pattern, causal_status="CAUSAL")
        inconsistent = dataclasses.replace(pattern, sample_size=2)
        current_wildcard = wildcard(self.hq)
        with self.assertRaisesRegex(ValidationError, "association-only"):
            self.service.challenge_pattern(
                song_id=self.song.id,
                pattern=causal,
                suggestion=current_wildcard,
            )
        with self.assertRaisesRegex(ValidationError, "sample size"):
            self.service.challenge_pattern(
                song_id=self.song.id,
                pattern=inconsistent,
                suggestion=current_wildcard,
            )

    def test_challenger_rejects_fabricated_pattern(self) -> None:
        pattern = canonical_pattern(self.hq, self.song.id)
        fabricated = dataclasses.replace(pattern, id="success_fabricated")
        with self.assertRaisesRegex(
            ValidationError, "not canonical current Learning synthesis"
        ):
            self.service.challenge_pattern(
                song_id=self.song.id,
                pattern=fabricated,
                suggestion=wildcard(self.hq),
            )

    def test_challenger_rejects_fabricated_or_stale_current_looking_suggestion(
        self,
    ) -> None:
        pattern = canonical_pattern(self.hq, self.song.id)
        canonical = wildcard(self.hq)
        for fabricated in (
            dataclasses.replace(canonical, prompt="Fabricated prompt"),
            dataclasses.replace(canonical, semantic_key="sound:fabricated"),
            dataclasses.replace(canonical, title="Fabricated title"),
            dataclasses.replace(canonical, session_id="session_fabricated"),
            dataclasses.replace(canonical, song_title="Fabricated Song title"),
        ):
            with self.subTest(fabricated=fabricated):
                with self.assertRaisesRegex(
                    ValidationError,
                    "not canonical current deterministic Wildcard",
                ):
                    self.service.challenge_pattern(
                        song_id=self.song.id,
                        pattern=pattern,
                        suggestion=fabricated,
                    )

    def test_challenger_rejects_provider_execution_and_authorized_suggestion(
        self,
    ) -> None:
        pattern = canonical_pattern(self.hq, self.song.id)
        with self.assertRaisesRegex(
            ValidationError, "provider-executed"
        ):
            self.service.challenge_pattern(
                song_id=self.song.id,
                pattern=pattern,
                suggestion=wildcard(
                    self.hq, provider_used=True
                ),
            )
        with self.assertRaisesRegex(ValidationError, "action authority"):
            self.service.challenge_pattern(
                song_id=self.song.id,
                pattern=pattern,
                suggestion=wildcard(
                    self.hq, authority=True
                ),
            )

    def test_malformed_manual_evidence_claim_cannot_cross_kernel_boundary(
        self,
    ) -> None:
        bad_source = EvidenceClaim(
            id="claim_bad_source",
            sequence=1,
            scope_kind="SONG",
            scope_id=self.song.id,
            key="energy",
            value="dense",
            source_kind="CERTAIN",
            source_ref="fake:1",
            confidence=1.0,
            twin_domain="TECHNICAL",
        )
        with self.assertRaisesRegex(
            ValidationError, "source semantics"
        ):
            self.service.frame_tension(
                song_id=self.song.id,
                value_a="density",
                value_b="space",
                reason="test",
                technical_claims=(bad_source,),
            )

        bad_confidence = EvidenceClaim(
            id="claim_bad_confidence",
            sequence=2,
            scope_kind="SONG",
            scope_id=self.song.id,
            key="energy",
            value="dense",
            source_kind="OBSERVED",
            source_ref="fake:2",
            confidence=True,
            twin_domain="TECHNICAL",
        )
        with self.assertRaisesRegex(
            ValidationError, "confidence must be numeric"
        ):
            self.service.frame_tension(
                song_id=self.song.id,
                value_a="density",
                value_b="space",
                reason="test",
                technical_claims=(bad_confidence,),
            )

        nan_confidence = dataclasses.replace(
            bad_confidence,
            id="claim_nan_confidence",
            confidence=math.nan,
        )
        with self.assertRaisesRegex(
            ValidationError, "confidence must be finite"
        ):
            self.service.frame_tension(
                song_id=self.song.id,
                value_a="density",
                value_b="space",
                reason="test",
                technical_claims=(nan_confidence,),
            )

    def test_result_authority_and_persistence_fields_are_hard_false(
        self,
    ) -> None:
        tension = self.service.frame_tension(
            song_id=self.song.id,
            value_a="clarity",
            value_b="character",
            reason="test",
        )
        experiment = self.service.challenge_pattern(
            song_id=self.song.id,
            pattern=canonical_pattern(self.hq, self.song.id),
            suggestion=wildcard(self.hq),
        )
        audience = self.service.separate_audience_evidence(
            song_id=self.song.id
        )

        for result in (tension, experiment, audience):
            self.assertFalse(result.durable)
            self.assertFalse(result.action_authority_granted)
            self.assertFalse(result.mutation_authorized)
            with self.assertRaises(dataclasses.FrozenInstanceError):
                result.action_authority_granted = True

        self.assertFalse(
            CreativeTensionHypothesis.__dataclass_fields__[
                "action_authority_granted"
            ].init
        )
        self.assertFalse(
            ChallengerExperiment.__dataclass_fields__[
                "action_authority_granted"
            ].init
        )
        self.assertFalse(
            AudienceEvidenceView.__dataclass_fields__[
                "action_authority_granted"
            ].init
        )

    def test_kernel_reads_do_not_mutate_canonical_store(self) -> None:
        technical = self.hq.evidence.record_claim(
            scope_kind="SONG",
            scope_id=self.song.id,
            key="texture",
            value="rough",
            source_kind="OBSERVED",
            twin_domain="TECHNICAL",
        )
        creative = self.hq.evidence.record_claim(
            scope_kind="SONG",
            scope_id=self.song.id,
            key="texture",
            value="rough on purpose",
            source_kind="USER_DECLARED",
            twin_domain="CREATIVE",
        )
        pattern = canonical_pattern(self.hq, self.song.id)
        changes = self.hq.store._conn.total_changes

        self.service.frame_tension(
            song_id=self.song.id,
            value_a="polish",
            value_b="character",
            reason="keep the rough edge if it serves the Song",
            technical_claims=(technical,),
            creative_claims=(creative,),
        )
        self.service.challenge_pattern(
            song_id=self.song.id,
            pattern=pattern,
            suggestion=wildcard(self.hq),
        )
        self.service.separate_audience_evidence(
            song_id=self.song.id,
            evidence=(
                AudienceEvidence(
                    song_id=self.song.id,
                    version_id=None,
                    evidence_class="SIMULATED_FIRST_LISTEN",
                    source_kind="MODEL_INFERENCE",
                    source_ref="simulation:1",
                    signal="UNKNOWN",
                    summary="No confident first-listen inference.",
                    confidence=0.3,
                ),
            ),
        )

        self.assertEqual(changes, self.hq.store._conn.total_changes)


if __name__ == "__main__":
    unittest.main()
