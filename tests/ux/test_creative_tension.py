from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from n0te2 import HeadquartersMemory, ValidationError
from n0te2.creative_suggestions import CreativeSuggestion, CreativeSuggestionService
from n0te2.creative_tension import AudienceEvidence, CreativeTensionService
from n0te2.evidence import EvidenceClaim


def seed_success_pattern(
    hq: HeadquartersMemory,
    song_id: str,
    *,
    change: str = "leave one support layer out before the chorus",
):
    session = hq.sessions.start_session(
        song_id=song_id,
        objective="Test one bounded change",
    )
    episode = hq.learning.create_episode(
        session_id=session.id,
        domain="ARRANGEMENT",
        subject_ref="chorus",
        change_description=change,
    )
    hq.learning.append_consequence(
        episode.id,
        observation="The chorus arrival read more clearly.",
        source_kind="OBSERVED",
        source_ref=f"test:{episode.id}",
        confidence=0.8,
        conditions=("same arrangement",),
        confounders=("single listen",),
    )
    hq.learning.decide(
        episode.id,
        decision="KEEP",
        rationale="Keep this version for the experiment.",
        confidence=0.85,
    )
    hq.sessions.close_session(
        session.id,
        debrief_summary="The bounded Learning experiment is complete.",
        next_action="Review the next Song experiment independently.",
    )
    return next(
        item
        for item in hq.success.patterns_for_song(song_id)
        if item.change_description == change
    )


def suggestion(
    hq: HeadquartersMemory, *, distance: str = "WILDCARD"
) -> CreativeSuggestion:
    return CreativeSuggestionService(hq.store, hq.sessions).suggest(
        distance=distance,
        locked_dimensions=(),
        variation=0,
    )


class CreativeTensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hq = HeadquartersMemory.create(self.root, "Artist")
        self.addCleanup(self.hq.close)
        self.song = self.hq.store.create_song("Song")
        self.version = self.hq.store.create_version(
            self.song.id, label="v1"
        )
        self.service = CreativeTensionService(
            self.hq.store, self.hq.evidence, self.hq.success
        )

    def test_tension_keeps_twin_lanes_separate_and_never_selects_winner(
        self,
    ) -> None:
        technical = self.hq.evidence.record_claim(
            scope_kind="VERSION",
            scope_id=self.version.id,
            key="timing.feel",
            value="off-grid",
            source_kind="MEASURED",
            source_ref="analysis:timing",
            twin_domain="TECHNICAL",
        )
        creative = self.hq.evidence.record_claim(
            scope_kind="SONG",
            scope_id=self.song.id,
            key="timing.feel",
            value="intentional push",
            source_kind="USER_DECLARED",
            source_ref="artist:intent",
            twin_domain="CREATIVE",
        )

        result = self.service.frame_tension(
            song_id=self.song.id,
            version_id=self.version.id,
            value_a="grid precision",
            value_b="human push",
            reason=(
                "The groove is technically irregular but the push is intentional."
            ),
            technical_claims=(technical,),
            creative_claims=(creative,),
        )

        self.assertEqual(result.truth_state, "HYPOTHESIS")
        self.assertIsNone(result.winner)
        self.assertEqual(result.technical_claim_ids, (technical.id,))
        self.assertEqual(result.creative_claim_ids, (creative.id,))
        self.assertFalse(
            result.technical_evidence_decides_artistic_preference
        )
        self.assertFalse(result.durable)
        self.assertFalse(result.action_authority_granted)
        self.assertFalse(result.mutation_authorized)
        self.assertIn(
            "not as a defect-versus-correctness verdict", result.framing
        )
        self.assertIn(
            "neither lane chooses the artistic winner", result.framing
        )

    def test_foreign_scope_and_cross_twin_evidence_fail_closed(
        self,
    ) -> None:
        other = self.hq.store.create_song("Other")
        foreign = self.hq.evidence.record_claim(
            scope_kind="SONG",
            scope_id=other.id,
            key="energy",
            value="dense",
            source_kind="OBSERVED",
            twin_domain="TECHNICAL",
        )
        creative = self.hq.evidence.record_claim(
            scope_kind="SONG",
            scope_id=self.song.id,
            key="energy",
            value="restrained on purpose",
            source_kind="USER_DECLARED",
            twin_domain="CREATIVE",
        )

        with self.assertRaisesRegex(ValidationError, "different scope"):
            self.service.frame_tension(
                song_id=self.song.id,
                value_a="density",
                value_b="restraint",
                reason="test",
                technical_claims=(foreign,),
            )
        with self.assertRaisesRegex(
            ValidationError, "crossed Twin domains"
        ):
            self.service.frame_tension(
                song_id=self.song.id,
                value_a="density",
                value_b="restraint",
                reason="test",
                technical_claims=(creative,),
            )

    def test_fabricated_valid_looking_claim_is_not_accepted_as_evidence(
        self,
    ) -> None:
        fabricated = EvidenceClaim(
            id="claim_fabricated",
            sequence=999,
            scope_kind="SONG",
            scope_id=self.song.id,
            key="energy",
            value="dense",
            source_kind="OBSERVED",
            source_ref="fake:canonical-looking",
            confidence=0.9,
            twin_domain="TECHNICAL",
        )
        with self.assertRaisesRegex(ValidationError, "not canonical"):
            self.service.frame_tension(
                song_id=self.song.id,
                value_a="density",
                value_b="space",
                reason="test",
                technical_claims=(fabricated,),
            )

    def test_superseded_claim_cannot_reenter_current_tension_evidence(
        self,
    ) -> None:
        old = self.hq.evidence.record_claim(
            scope_kind="SONG",
            scope_id=self.song.id,
            key="energy",
            value="dense",
            source_kind="OBSERVED",
            source_ref="analysis:old",
            confidence=0.8,
            twin_domain="TECHNICAL",
        )
        self.hq.evidence.record_claim(
            scope_kind="SONG",
            scope_id=self.song.id,
            key="energy",
            value="sparse",
            source_kind="OBSERVED",
            source_ref="analysis:new",
            confidence=0.9,
            twin_domain="TECHNICAL",
            supersedes=(old.id,),
        )
        with self.assertRaisesRegex(
            ValidationError, "superseded or inactive"
        ):
            self.service.frame_tension(
                song_id=self.song.id,
                value_a="density",
                value_b="space",
                reason="test",
                technical_claims=(old,),
            )

    def test_version_specific_evidence_requires_exact_version_context(
        self,
    ) -> None:
        technical = self.hq.evidence.record_claim(
            scope_kind="VERSION",
            scope_id=self.version.id,
            key="width",
            value="narrow",
            source_kind="MEASURED",
            twin_domain="TECHNICAL",
        )
        with self.assertRaisesRegex(ValidationError, "different scope"):
            self.service.frame_tension(
                song_id=self.song.id,
                value_a="focus",
                value_b="width",
                reason="test",
                technical_claims=(technical,),
            )

    def test_challenger_uses_only_canonical_association_as_bounded_experiment(
        self,
    ) -> None:
        pattern = seed_success_pattern(self.hq, self.song.id)
        wildcard = suggestion(self.hq)

        result = self.service.challenge_pattern(
            song_id=self.song.id,
            pattern=pattern,
            suggestion=wildcard,
        )

        self.assertEqual(result.causal_status, "ASSOCIATION_ONLY")
        self.assertEqual(result.pattern_id, pattern.id)
        self.assertEqual(
            result.suggestion_semantic_key, wildcard.semantic_key
        )
        self.assertEqual(result.suggestion_dimension, wildcard.dimension)
        self.assertEqual(result.suggestion_prompt, wildcard.prompt)
        self.assertFalse(result.recommendation)
        self.assertFalse(result.durable)
        self.assertFalse(result.action_authority_granted)
        self.assertFalse(result.mutation_authorized)
        self.assertIn("bounded experiment against habit", result.rationale)
        self.assertIn("not proof", result.rationale)

    def test_challenger_rejects_fabricated_current_looking_suggestion(
        self,
    ) -> None:
        pattern = seed_success_pattern(self.hq, self.song.id)
        canonical = suggestion(self.hq)
        fabricated = dataclasses.replace(
            canonical,
            prompt=canonical.prompt + " Fabricated extra instruction.",
        )
        with self.assertRaisesRegex(
            ValidationError, "not canonical current deterministic Wildcard"
        ):
            self.service.challenge_pattern(
                song_id=self.song.id,
                pattern=pattern,
                suggestion=fabricated,
            )

    def test_challenger_rejects_foreign_song_and_non_wildcard(
        self,
    ) -> None:
        other = self.hq.store.create_song("Other")
        foreign_pattern = seed_success_pattern(self.hq, other.id)
        with self.assertRaisesRegex(ValidationError, "different Song"):
            self.service.challenge_pattern(
                song_id=self.song.id,
                pattern=foreign_pattern,
                suggestion=suggestion(self.hq),
            )

        self.hq.store.select_song(self.song.id)
        with self.assertRaisesRegex(ValidationError, "Wildcard"):
            self.service.challenge_pattern(
                song_id=self.song.id,
                pattern=seed_success_pattern(
                    self.hq,
                    self.song.id,
                    change="leave the bass out for one beat",
                ),
                suggestion=suggestion(self.hq, distance="FAMILIAR"),
            )

    def test_stale_success_pattern_cannot_reenter_challenger(
        self,
    ) -> None:
        old = seed_success_pattern(
            self.hq,
            self.song.id,
            change="mute one support layer",
        )
        seed_success_pattern(
            self.hq,
            self.song.id,
            change="mute one support layer",
        )
        with self.assertRaisesRegex(
            ValidationError, "not canonical current Learning synthesis"
        ):
            self.service.challenge_pattern(
                song_id=self.song.id,
                pattern=old,
                suggestion=suggestion(self.hq),
            )

    def test_simulated_and_observed_audience_evidence_never_blend(
        self,
    ) -> None:
        simulated = AudienceEvidence(
            song_id=self.song.id,
            version_id=self.version.id,
            evidence_class="SIMULATED_FIRST_LISTEN",
            source_kind="MODEL_INFERENCE",
            source_ref="simulation:first-listen:1",
            signal="FAVORABLE",
            summary="The first chorus may read as immediate.",
            confidence=0.62,
        )
        observed = AudienceEvidence(
            song_id=self.song.id,
            version_id=self.version.id,
            evidence_class="OBSERVED_AUDIENCE",
            source_kind="LISTENER_SESSION",
            source_ref="listener-session:12",
            signal="UNFAVORABLE",
            summary="Three listeners lost the hook at the first chorus.",
            confidence=0.9,
            observed_at="2026-09-04T11:00:00Z",
        )

        result = self.service.separate_audience_evidence(
            song_id=self.song.id,
            version_id=self.version.id,
            evidence=(simulated, observed),
        )

        self.assertEqual(
            result.state, "BOTH_PRESENT_KEEP_SEPARATE"
        )
        self.assertEqual(result.simulated, (simulated,))
        self.assertEqual(result.observed, (observed,))
        self.assertIsNone(result.blended_score)
        self.assertFalse(result.market_certainty)
        self.assertFalse(result.hit_prediction)
        self.assertFalse(result.durable)
        self.assertFalse(result.action_authority_granted)
        self.assertFalse(result.mutation_authorized)

    def test_audience_version_scope_is_exact(self) -> None:
        other_version = self.hq.store.create_version(
            self.song.id, label="v2"
        )
        observed = AudienceEvidence(
            song_id=self.song.id,
            version_id=other_version.id,
            evidence_class="OBSERVED_AUDIENCE",
            source_kind="PLATFORM_METRIC",
            source_ref="platform:test",
            signal="MIXED",
            summary="Mixed observed response.",
            confidence=0.7,
            observed_at="2026-09-04T11:00:00Z",
        )
        with self.assertRaisesRegex(
            ValidationError, "different Song or Version"
        ):
            self.service.separate_audience_evidence(
                song_id=self.song.id,
                version_id=self.version.id,
                evidence=(observed,),
            )


if __name__ == "__main__":
    unittest.main()
