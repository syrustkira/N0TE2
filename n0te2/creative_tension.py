from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .creative_suggestions import (
    CREATIVE_DIMENSIONS,
    SUGGESTION_SOURCE_KIND,
    CreativeSuggestion,
    CreativeSuggestionService,
)
from .evidence import EvidenceClaim, EvidenceMemory, SCOPE_KINDS, SOURCE_KINDS
from .lineage import LineageStore, NotFoundError, ValidationError
from .success import CAUSAL_STATUS, HUMILITY_STATES, SuccessMemory, SuccessPattern

TENSION_TRUTH_STATE = "HYPOTHESIS"
AUDIENCE_EVIDENCE_CLASSES = {
    "SIMULATED_FIRST_LISTEN",
    "OBSERVED_AUDIENCE",
}
SIMULATED_AUDIENCE_SOURCES = {
    "MODEL_INFERENCE",
    "LOCAL_HEURISTIC",
}
OBSERVED_AUDIENCE_SOURCES = {
    "LISTENER_SESSION",
    "PLATFORM_METRIC",
    "LIVE_OBSERVATION",
    "SURVEY",
}
AUDIENCE_SIGNALS = {
    "FAVORABLE",
    "UNFAVORABLE",
    "MIXED",
    "NEUTRAL",
    "UNKNOWN",
}


def _text(value: object, field_name: str, *, max_length: int = 2000) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be text")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValidationError(f"{field_name} must not be empty")
    if len(normalized) > max_length:
        raise ValidationError(f"{field_name} exceeds the bounded text limit")
    return normalized


def _enum(value: object, field_name: str, allowed: set[str]) -> str:
    normalized = (
        _text(value, field_name, max_length=80)
        .upper()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if normalized not in allowed:
        raise ValidationError(f"unsupported {field_name}: {normalized}")
    return normalized


def _observed_timestamp(value: object) -> str:
    text = _text(value, "audience observed_at", max_length=200)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValidationError(
            "audience observed_at must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("audience observed_at must be timezone-aware")
    return text


@dataclass(frozen=True)
class AudienceEvidence:
    song_id: str
    version_id: str | None
    evidence_class: str
    source_kind: str
    source_ref: str
    signal: str
    summary: str
    confidence: float
    observed_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "song_id",
            _text(self.song_id, "audience song_id", max_length=200),
        )
        if self.version_id is not None:
            object.__setattr__(
                self,
                "version_id",
                _text(self.version_id, "audience version_id", max_length=200),
            )
        evidence_class = _enum(
            self.evidence_class,
            "audience evidence class",
            AUDIENCE_EVIDENCE_CLASSES,
        )
        object.__setattr__(self, "evidence_class", evidence_class)
        object.__setattr__(
            self,
            "signal",
            _enum(self.signal, "audience signal", AUDIENCE_SIGNALS),
        )
        object.__setattr__(
            self,
            "source_ref",
            _text(self.source_ref, "audience source_ref", max_length=1000),
        )
        object.__setattr__(
            self,
            "summary",
            _text(self.summary, "audience summary", max_length=2000),
        )
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, (int, float)
        ):
            raise ValidationError("audience confidence must be numeric")
        confidence = float(self.confidence)
        if not math.isfinite(confidence):
            raise ValidationError("audience confidence must be finite")
        if confidence < 0.0 or confidence > 1.0:
            raise ValidationError("audience confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)

        if evidence_class == "SIMULATED_FIRST_LISTEN":
            source_kind = _enum(
                self.source_kind,
                "simulated audience source kind",
                SIMULATED_AUDIENCE_SOURCES,
            )
            if self.observed_at is not None:
                raise ValidationError(
                    "simulated audience inference cannot carry observed_at"
                )
        else:
            source_kind = _enum(
                self.source_kind,
                "observed audience source kind",
                OBSERVED_AUDIENCE_SOURCES,
            )
            if self.observed_at is None:
                raise ValidationError(
                    "observed audience evidence requires observed_at"
                )
            object.__setattr__(
                self,
                "observed_at",
                _observed_timestamp(self.observed_at),
            )
        object.__setattr__(self, "source_kind", source_kind)


@dataclass(frozen=True)
class CreativeTensionHypothesis:
    song_id: str
    version_id: str | None
    value_a: str
    value_b: str
    reason: str
    framing: str
    technical_claim_ids: tuple[str, ...]
    creative_claim_ids: tuple[str, ...]
    truth_state: str = field(default=TENSION_TRUTH_STATE, init=False)
    winner: None = field(default=None, init=False)
    durable: bool = field(default=False, init=False)
    technical_evidence_decides_artistic_preference: bool = field(
        default=False, init=False
    )
    action_authority_granted: bool = field(default=False, init=False)
    mutation_authorized: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ChallengerExperiment:
    song_id: str
    pattern_id: str
    pattern_change: str
    pattern_humility_state: str
    pattern_warning: str
    suggestion_semantic_key: str
    suggestion_dimension: str
    suggestion_prompt: str
    rationale: str
    causal_status: str = field(default=CAUSAL_STATUS, init=False)
    recommendation: bool = field(default=False, init=False)
    durable: bool = field(default=False, init=False)
    action_authority_granted: bool = field(default=False, init=False)
    mutation_authorized: bool = field(default=False, init=False)


@dataclass(frozen=True)
class AudienceEvidenceView:
    song_id: str
    version_id: str | None
    simulated: tuple[AudienceEvidence, ...]
    observed: tuple[AudienceEvidence, ...]
    state: str
    blended_score: None = field(default=None, init=False)
    market_certainty: bool = field(default=False, init=False)
    hit_prediction: bool = field(default=False, init=False)
    durable: bool = field(default=False, init=False)
    action_authority_granted: bool = field(default=False, init=False)
    mutation_authorized: bool = field(default=False, init=False)


class CreativeTensionService:
    """Pure-read creative tension, Challenger and audience-truth kernel.

    The service frames tradeoffs and experiments around canonical evidence without
    deciding taste, persisting a tension, ranking a learned pattern, blending
    simulated audience inference with observed audience evidence, or granting
    authority to act.
    """

    def __init__(
        self,
        store: LineageStore,
        evidence: EvidenceMemory,
        success: SuccessMemory,
    ):
        if not isinstance(store, LineageStore):
            raise TypeError(
                "CreativeTensionService requires canonical LineageStore"
            )
        if not isinstance(evidence, EvidenceMemory) or evidence.store is not store:
            raise TypeError(
                "CreativeTensionService requires canonical EvidenceMemory for the same LineageStore"
            )
        if not isinstance(success, SuccessMemory) or success.learning.store is not store:
            raise TypeError(
                "CreativeTensionService requires canonical SuccessMemory for the same LineageStore"
            )
        sessions = success.learning.sessions
        if sessions.store is not store:
            raise TypeError(
                "CreativeTensionService requires canonical SessionMemory for the same LineageStore"
            )
        self.store = store
        self.evidence = evidence
        self.success = success
        self.suggestions = CreativeSuggestionService(store, sessions)

    def _context(self, song_id: str, version_id: str | None):
        song_key = _text(song_id, "song_id", max_length=200)
        song = self.store.get_song(song_key)
        if song is None:
            raise NotFoundError(
                f"Song not found in profile {self.store.profile_id}: {song_key}"
            )
        version_key = None
        if version_id is not None:
            version_key = _text(version_id, "version_id", max_length=200)
            version = self.store.get_version(version_key)
            if version is None:
                raise NotFoundError(f"version not found: {version_key}")
            if version.song_id != song.id:
                raise ValidationError("version belongs to a different Song")
        return song, version_key

    @staticmethod
    def _validate_claim_shape(
        claim: EvidenceClaim, expected_domain: str
    ) -> None:
        if not isinstance(claim, EvidenceClaim):
            raise TypeError("creative tension evidence must be EvidenceClaim")
        if claim.scope_kind not in SCOPE_KINDS:
            raise ValidationError(
                "creative tension evidence has unsupported scope"
            )
        if claim.source_kind not in SOURCE_KINDS:
            raise ValidationError(
                "creative tension evidence has unsupported source semantics"
            )
        if claim.twin_domain != expected_domain:
            raise ValidationError(
                f"creative tension {expected_domain.lower()} evidence crossed Twin domains"
            )
        if not isinstance(claim.key, str) or not claim.key.strip():
            raise ValidationError(
                "creative tension evidence key must not be empty"
            )
        if isinstance(claim.confidence, bool) or not isinstance(
            claim.confidence, (int, float)
        ):
            raise ValidationError(
                "creative tension evidence confidence must be numeric"
            )
        confidence = float(claim.confidence)
        if not math.isfinite(confidence):
            raise ValidationError(
                "creative tension evidence confidence must be finite"
            )
        if confidence < 0.0 or confidence > 1.0:
            raise ValidationError(
                "creative tension evidence confidence must be between 0 and 1"
            )
        if claim.source_ref is not None and (
            not isinstance(claim.source_ref, str)
            or not claim.source_ref.strip()
        ):
            raise ValidationError(
                "creative tension evidence source_ref must be non-empty text when present"
            )

    def _validate_claim_context(
        self,
        *,
        song,
        version_id: str | None,
        claim: EvidenceClaim,
        expected_domain: str,
    ) -> None:
        self._validate_claim_shape(claim, expected_domain)
        expected = {
            "PROFILE": self.store.profile_id,
            "ARTIST": song.artist_id,
            "SONG": song.id,
            "VERSION": version_id,
        }[claim.scope_kind]
        if expected is None or claim.scope_id != expected:
            raise ValidationError(
                "creative tension evidence belongs to a different scope"
            )
        canonical = self.evidence.get_claim(claim.id)
        if canonical is None or canonical != claim:
            raise ValidationError(
                "creative tension evidence is not canonical for this profile"
            )
        active_ids = {
            item.id
            for item in self.evidence.active_claims(
                claim.scope_kind, claim.scope_id, claim.key
            )
        }
        if claim.id not in active_ids:
            raise ValidationError(
                "creative tension evidence is superseded or inactive"
            )

    def frame_tension(
        self,
        *,
        song_id: str,
        version_id: str | None = None,
        value_a: str,
        value_b: str,
        reason: str,
        technical_claims: Iterable[EvidenceClaim] = (),
        creative_claims: Iterable[EvidenceClaim] = (),
    ) -> CreativeTensionHypothesis:
        song, version_key = self._context(song_id, version_id)
        first = _text(value_a, "first creative value", max_length=200)
        second = _text(value_b, "second creative value", max_length=200)
        if first.casefold() == second.casefold():
            raise ValidationError(
                "creative tension requires two distinct values"
            )
        why = _text(reason, "creative tension reason", max_length=2000)

        technical = tuple(technical_claims)
        creative = tuple(creative_claims)
        for claim in technical:
            self._validate_claim_context(
                song=song,
                version_id=version_key,
                claim=claim,
                expected_domain="TECHNICAL",
            )
        for claim in creative:
            self._validate_claim_context(
                song=song,
                version_id=version_key,
                claim=claim,
                expected_domain="CREATIVE",
            )

        framing = (
            f"Treat {first} and {second} as competing artistic values to test, not "
            "as a defect-versus-correctness verdict. Technical evidence can describe "
            "the current state, while creative evidence can describe intent; neither "
            "lane chooses the artistic winner."
        )
        return CreativeTensionHypothesis(
            song_id=song.id,
            version_id=version_key,
            value_a=first,
            value_b=second,
            reason=why,
            framing=framing,
            technical_claim_ids=tuple(claim.id for claim in technical),
            creative_claim_ids=tuple(claim.id for claim in creative),
        )

    def _validate_success_pattern(
        self, pattern: SuccessPattern, song_id: str
    ) -> None:
        if not isinstance(pattern, SuccessPattern):
            raise TypeError(
                "Challenger requires canonical SuccessPattern"
            )
        if pattern.causal_status != CAUSAL_STATUS:
            raise ValidationError(
                "Challenger refuses success evidence that is not association-only"
            )
        if pattern.humility_state not in HUMILITY_STATES:
            raise ValidationError(
                "Challenger received unknown Success humility state"
            )
        if song_id not in pattern.song_ids:
            raise ValidationError(
                "Success pattern belongs to a different Song"
            )
        counts = (
            pattern.keep_count,
            pattern.revert_count,
            pattern.revise_count,
            pattern.inconclusive_count,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in counts
        ):
            raise ValidationError(
                "Success pattern decision counts are malformed"
            )
        if pattern.sample_size != sum(counts):
            raise ValidationError(
                "Success pattern sample size does not match its decision evidence"
            )
        if pattern.sample_size <= 0 or pattern.keep_count <= 0:
            raise ValidationError(
                "Challenger requires at least one retained completed example before "
                "calling a pattern a learned habit"
            )
        for field_name, value in (
            ("pattern id", pattern.id),
            ("pattern domain", pattern.domain),
            ("pattern subject", pattern.subject_ref),
            ("pattern change", pattern.change_description),
            ("pattern warning", pattern.warning),
        ):
            _text(value, field_name, max_length=2000)
        canonical = {
            item.id: item for item in self.success.patterns_for_song(song_id)
        }.get(pattern.id)
        if canonical is None or canonical != pattern:
            raise ValidationError(
                "Challenger success pattern is not canonical current Learning synthesis"
            )

    def _validate_wildcard_suggestion(
        self, suggestion: CreativeSuggestion, song_id: str
    ) -> None:
        if not isinstance(suggestion, CreativeSuggestion):
            raise TypeError(
                "Challenger requires bounded CreativeSuggestion"
            )
        if suggestion.song_id != song_id:
            raise ValidationError(
                "Challenger suggestion belongs to a different Song"
            )
        if suggestion.distance != "WILDCARD":
            raise ValidationError(
                "Challenger requires a Wildcard suggestion for deliberate "
                "counter-habit exploration"
            )
        if suggestion.dimension not in CREATIVE_DIMENSIONS:
            raise ValidationError(
                "Challenger suggestion has unknown creative dimension"
            )
        if suggestion.source_kind != SUGGESTION_SOURCE_KIND:
            raise ValidationError(
                "Challenger suggestion source semantics changed"
            )
        if suggestion.provider_used:
            raise ValidationError(
                "this local Challenger kernel does not accept provider-executed "
                "suggestions"
            )
        if suggestion.action_authority_granted:
            raise ValidationError(
                "a suggestion with action authority cannot be reused as "
                "Challenger evidence"
            )
        _text(
            suggestion.semantic_key,
            "suggestion semantic_key",
            max_length=500,
        )
        _text(
            suggestion.prompt,
            "suggestion prompt",
            max_length=4000,
        )

        active = self.store.active_song()
        if active is None or active.id != song_id:
            raise ValidationError(
                "Challenger requires the requested Song to be the active Song"
            )
        expected = self.suggestions.suggest(
            distance="WILDCARD",
            locked_dimensions=(),
            variation=0,
        )
        if suggestion != expected:
            raise ValidationError(
                "Challenger suggestion is not canonical current deterministic Wildcard"
            )

    def challenge_pattern(
        self,
        *,
        song_id: str,
        pattern: SuccessPattern,
        suggestion: CreativeSuggestion,
    ) -> ChallengerExperiment:
        song, _ = self._context(song_id, None)
        self._validate_success_pattern(pattern, song.id)
        self._validate_wildcard_suggestion(suggestion, song.id)

        rationale = (
            "Success Memory marks the retained pattern as ASSOCIATION_ONLY. "
            "Challenger therefore treats the Wildcard as one bounded experiment "
            "against habit, not proof that the past pattern was causal, wrong, or "
            "something the artist should stop doing."
        )
        return ChallengerExperiment(
            song_id=song.id,
            pattern_id=pattern.id,
            pattern_change=pattern.change_description,
            pattern_humility_state=pattern.humility_state,
            pattern_warning=pattern.warning,
            suggestion_semantic_key=suggestion.semantic_key,
            suggestion_dimension=suggestion.dimension,
            suggestion_prompt=suggestion.prompt,
            rationale=rationale,
        )

    def separate_audience_evidence(
        self,
        *,
        song_id: str,
        version_id: str | None = None,
        evidence: Iterable[AudienceEvidence] = (),
    ) -> AudienceEvidenceView:
        song, version_key = self._context(song_id, version_id)
        simulated: list[AudienceEvidence] = []
        observed: list[AudienceEvidence] = []

        for item in tuple(evidence):
            if not isinstance(item, AudienceEvidence):
                raise TypeError(
                    "audience evidence must use AudienceEvidence"
                )
            if item.song_id != song.id or item.version_id != version_key:
                raise ValidationError(
                    "audience evidence belongs to a different Song or Version scope"
                )
            if item.version_id is not None:
                version = self.store.get_version(item.version_id)
                if version is None or version.song_id != song.id:
                    raise ValidationError(
                        "audience evidence Version does not belong to this Song"
                    )
            if item.evidence_class == "SIMULATED_FIRST_LISTEN":
                simulated.append(item)
            elif item.evidence_class == "OBSERVED_AUDIENCE":
                observed.append(item)
            else:
                raise ValidationError("unknown audience evidence class")

        if simulated and observed:
            state = "BOTH_PRESENT_KEEP_SEPARATE"
        elif simulated:
            state = "SIMULATED_ONLY"
        elif observed:
            state = "OBSERVED_ONLY"
        else:
            state = "NO_EVIDENCE"

        return AudienceEvidenceView(
            song_id=song.id,
            version_id=version_key,
            simulated=tuple(simulated),
            observed=tuple(observed),
            state=state,
        )
