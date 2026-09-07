from __future__ import annotations

import pytest

from n0te2.catalog_topology import (
    CatalogEvidence,
    CatalogSongCandidate,
    assess_catalog_song,
    prioritize_catalog,
)
from n0te2.lineage import ValidationError


def ev(
    evidence_id: str,
    song_id: str,
    kind: str,
    value: str,
    *,
    source_kind: str = "OBSERVED",
    freshness_state: str = "CURRENT",
    source_ref: str | None = None,
) -> CatalogEvidence:
    return CatalogEvidence(
        id=evidence_id,
        song_id=song_id,
        kind=kind,
        value=value,
        source_kind=source_kind,
        source_ref=source_ref or f"source:{evidence_id}",
        freshness_state=freshness_state,
    )


def core(
    song_id: str,
    *,
    readiness: str,
    blocker: str = "NONE",
    intent: str = "ALIGNED",
) -> tuple[CatalogEvidence, ...]:
    return (
        ev(f"{song_id}-readiness", song_id, "READINESS", readiness),
        ev(f"{song_id}-blocker", song_id, "BLOCKER", blocker),
        ev(
            f"{song_id}-intent",
            song_id,
            "ARTIST_INTENT",
            intent,
            source_kind="ARTIST_DECLARED",
        ),
    )


def test_delivery_ready_song_surfaces_preparation_options_without_authority() -> None:
    song_id = "song-ready"
    candidate = CatalogSongCandidate(
        song_id,
        evidence=(
            *core(song_id, readiness="DELIVERY_READY"),
            ev(song_id + "-target", song_id, "TARGET_FIT", "ALIGNED"),
            ev(song_id + "-relation", song_id, "CATALOG_RELATION", "COMPLEMENTARY"),
        ),
    )

    assessment = assess_catalog_song(candidate)

    assert assessment.priority_band == "READY_TO_DECIDE"
    assert assessment.dispositions == ("RELEASE_PREP", "PITCH_PREP", "GROUP")
    assert "READINESS_DELIVERY_READY" in assessment.reason_codes
    assert "TARGET_FIT_ALIGNED" in assessment.reason_codes
    assert assessment.external_action_authorized is False
    assert assessment.release_authorized is False
    assert assessment.pitch_authorized is False
    assert "no release, pitch, send or provider action is authorized" in assessment.smallest_next_step


def test_material_blocker_preserves_song_as_hold_instead_of_rejecting_it() -> None:
    song_id = "song-blocked"
    assessment = assess_catalog_song(
        CatalogSongCandidate(
            song_id,
            evidence=core(song_id, readiness="DELIVERY_READY", blocker="MATERIAL"),
        )
    )

    assert assessment.priority_band == "BLOCKED"
    assert assessment.dispositions == ("HOLD",)
    assert assessment.reason_codes == ("MATERIAL_BLOCKER", "OPTION_VALUE_PRESERVED")
    assert "preserves the Song rather than rejecting it" in assessment.smallest_next_step


def test_inference_stays_visible_but_cannot_green_missing_current_truth() -> None:
    song_id = "song-inferred"
    candidate = CatalogSongCandidate(
        song_id,
        evidence=(
            ev(
                "infer-ready",
                song_id,
                "READINESS",
                "DELIVERY_READY",
                source_kind="INFERRED",
            ),
            ev("blocker-none", song_id, "BLOCKER", "NONE"),
            ev(
                "intent-declared",
                song_id,
                "ARTIST_INTENT",
                "ALIGNED",
                source_kind="ARTIST_DECLARED",
            ),
        ),
    )

    assessment = assess_catalog_song(candidate)

    assert assessment.priority_band == "INSUFFICIENT_EVIDENCE"
    assert assessment.dispositions == ("NEED_MORE_EVIDENCE",)
    assert assessment.unknown_kinds == ("READINESS",)
    assert assessment.provisional_evidence_ids == ("infer-ready",)
    assert "infer-ready" not in assessment.current_evidence_ids


def test_stale_evidence_remains_history_but_does_not_drive_current_priority() -> None:
    song_id = "song-stale"
    candidate = CatalogSongCandidate(
        song_id,
        evidence=(
            ev(
                "stale-ready",
                song_id,
                "READINESS",
                "DELIVERY_READY",
                freshness_state="STALE",
            ),
            ev("blocker-none", song_id, "BLOCKER", "NONE"),
            ev(
                "intent-current",
                song_id,
                "ARTIST_INTENT",
                "ALIGNED",
                source_kind="ARTIST_DECLARED",
            ),
        ),
    )

    assessment = assess_catalog_song(candidate)

    assert assessment.priority_band == "INSUFFICIENT_EVIDENCE"
    assert assessment.stale_or_unknown_evidence_ids == ("stale-ready",)
    assert assessment.unknown_kinds == ("READINESS",)


def test_conflicting_current_evidence_is_not_collapsed_into_false_precision() -> None:
    song_id = "song-conflict"
    candidate = CatalogSongCandidate(
        song_id,
        evidence=(
            *core(song_id, readiness="REVIEWABLE"),
            ev("ready-conflict", song_id, "READINESS", "DELIVERY_READY"),
        ),
    )

    assessment = assess_catalog_song(candidate)

    assert assessment.priority_band == "INSUFFICIENT_EVIDENCE"
    assert assessment.dispositions == ("NEED_MORE_EVIDENCE",)
    assert assessment.reason_codes == ("CONFLICTING_CURRENT_EVIDENCE",)
    assert len(assessment.conflicts) == 1
    assert assessment.conflicts[0].kind == "READINESS"
    assert assessment.conflicts[0].values == ("DELIVERY_READY", "REVIEWABLE")
    assert "explicit correction or newer observation" in assessment.smallest_next_step


def test_developing_identity_experiment_is_an_option_not_a_quality_verdict() -> None:
    song_id = "song-experiment"
    candidate = CatalogSongCandidate(
        song_id,
        evidence=(
            *core(song_id, readiness="DEVELOPING"),
            ev("identity-exp", song_id, "IDENTITY_FIT", "EXPERIMENTAL"),
        ),
    )

    assessment = assess_catalog_song(candidate)

    assert assessment.priority_band == "ACTIVE_DEVELOPMENT"
    assert assessment.dispositions == ("EXPERIMENT_NEXT", "FINISH")
    assert "IDENTITY_EXPERIMENT" in assessment.reason_codes
    assert assessment.external_action_authorized is False


def test_same_band_is_a_tie_and_sort_order_does_not_masquerade_as_ranking() -> None:
    topology = prioritize_catalog(
        (
            CatalogSongCandidate(
                "song-z",
                evidence=core("song-z", readiness="DEVELOPING"),
            ),
            CatalogSongCandidate(
                "song-a",
                evidence=core("song-a", readiness="REVIEWABLE"),
            ),
        )
    )

    assert len(topology.groups) == 1
    group = topology.groups[0]
    assert group.priority_band == "ACTIVE_DEVELOPMENT"
    assert group.song_ids == ("song-a", "song-z")
    assert group.semantically_tied is True
    assert "not given a predictive or artistic rank" in group.ordering_note
    assert topology.predictive_hit_score_available is False
    assert topology.external_action_authorized is False


def test_popularity_streams_and_tenure_cannot_be_smuggled_in_as_ar_truth() -> None:
    for kind in ("FOLLOWER_COUNT", "STREAM_COUNT", "YEARS_ACTIVE"):
        with pytest.raises(ValidationError, match="unsupported catalog evidence kind"):
            ev("vanity-" + kind, "song-1", kind, "HIGH")


def test_cross_song_duplicate_and_malformed_identity_fail_closed() -> None:
    with pytest.raises(ValidationError, match="different Song"):
        CatalogSongCandidate(
            "song-a",
            evidence=(ev("wrong-song", "song-b", "READINESS", "DEVELOPING"),),
        )

    duplicate_evidence = ev("same-id", "song-a", "READINESS", "DEVELOPING")
    with pytest.raises(ValidationError, match="unique within a Song"):
        CatalogSongCandidate("song-a", evidence=(duplicate_evidence, duplicate_evidence))

    with pytest.raises(ValidationError, match="Song IDs must be unique"):
        prioritize_catalog(
            (
                CatalogSongCandidate("song-a", evidence=()),
                CatalogSongCandidate("song-a", evidence=()),
            )
        )


def test_duplicate_evidence_ids_across_songs_fail_closed() -> None:
    first = CatalogSongCandidate(
        "song-a",
        evidence=(ev("shared-id", "song-a", "READINESS", "DEVELOPING"),),
    )
    second = CatalogSongCandidate(
        "song-b",
        evidence=(ev("shared-id", "song-b", "READINESS", "REVIEWABLE"),),
    )

    with pytest.raises(ValidationError, match="globally unique"):
        prioritize_catalog((first, second))


def test_explicit_unknown_core_value_does_not_become_negative_or_positive_rank() -> None:
    song_id = "song-unknown"
    candidate = CatalogSongCandidate(
        song_id,
        evidence=(
            ev("unknown-ready", song_id, "READINESS", "UNKNOWN"),
            ev("blocker-none", song_id, "BLOCKER", "NONE"),
            ev(
                "intent-aligned",
                song_id,
                "ARTIST_INTENT",
                "ALIGNED",
                source_kind="ARTIST_DECLARED",
            ),
        ),
    )

    assessment = assess_catalog_song(candidate)

    assert assessment.priority_band == "INSUFFICIENT_EVIDENCE"
    assert assessment.unknown_kinds == ("READINESS",)
    assert assessment.dispositions == ("NEED_MORE_EVIDENCE",)


def test_non_text_identity_and_source_values_fail_closed_without_string_coercion() -> None:
    with pytest.raises(ValidationError, match="id must be text"):
        CatalogEvidence(
            id=None,  # type: ignore[arg-type]
            song_id="song-1",
            kind="READINESS",
            value="DEVELOPING",
            source_kind="OBSERVED",
            source_ref="source:1",
        )
    with pytest.raises(ValidationError, match="source_ref must be text"):
        CatalogEvidence(
            id="ev-1",
            song_id="song-1",
            kind="READINESS",
            value="DEVELOPING",
            source_kind="OBSERVED",
            source_ref=None,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError, match="candidate song_id must be text"):
        CatalogSongCandidate(None)  # type: ignore[arg-type]


def test_non_evidence_candidate_rows_fail_with_validation_error() -> None:
    with pytest.raises(ValidationError, match="must be CatalogEvidence"):
        CatalogSongCandidate("song-1", evidence=(object(),))  # type: ignore[arg-type]


def test_subjective_artist_truth_cannot_be_certified_by_external_or_measured_sources() -> None:
    with pytest.raises(ValidationError, match="cannot establish ARTIST_INTENT"):
        ev(
            "external-intent",
            "song-1",
            "ARTIST_INTENT",
            "ALIGNED",
            source_kind="VERIFIED_EXTERNAL",
        )
    with pytest.raises(ValidationError, match="unsupported catalog evidence source"):
        ev(
            "measured-identity",
            "song-1",
            "IDENTITY_FIT",
            "ALIGNED",
            source_kind="MEASURED",
        )


def test_confidence_is_strictly_numeric_and_not_semantic_string_coercion() -> None:
    with pytest.raises(ValidationError, match="confidence must be between 0 and 1"):
        CatalogEvidence(
            id="ev-string-confidence",
            song_id="song-1",
            kind="READINESS",
            value="DEVELOPING",
            source_kind="OBSERVED",
            source_ref="source:1",
            confidence="0.9",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError, match="confidence must be between 0 and 1"):
        CatalogEvidence(
            id="ev-bool-confidence",
            song_id="song-1",
            kind="READINESS",
            value="DEVELOPING",
            source_kind="OBSERVED",
            source_ref="source:1",
            confidence=True,  # type: ignore[arg-type]
        )


def test_malformed_evidence_semantics_fail_closed() -> None:
    with pytest.raises(ValidationError, match="unsupported READINESS"):
        ev("bad-ready", "song-1", "READINESS", "MASTERPIECE")
    with pytest.raises(ValidationError, match="unsupported catalog evidence source"):
        ev(
            "bad-source",
            "song-1",
            "READINESS",
            "DEVELOPING",
            source_kind="POPULARITY_MODEL",
        )
    with pytest.raises(ValidationError, match="unsupported catalog evidence freshness"):
        ev(
            "bad-fresh",
            "song-1",
            "READINESS",
            "DEVELOPING",
            freshness_state="PROBABLY_CURRENT",
        )
    with pytest.raises(ValidationError, match="at least one Song candidate"):
        prioritize_catalog(())
