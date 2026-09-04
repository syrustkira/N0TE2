from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .lineage import ValidationError

CATALOG_EVIDENCE_KINDS = {
    "READINESS",
    "BLOCKER",
    "ARTIST_INTENT",
    "IDENTITY_FIT",
    "CATALOG_RELATION",
    "TARGET_FIT",
}
CATALOG_SOURCE_KINDS = {
    "ARTIST_DECLARED",
    "OBSERVED",
    "VERIFIED_EXTERNAL",
    "INFERRED",
}
CATALOG_FRESHNESS_STATES = {"CURRENT", "STALE", "UNKNOWN"}
CATALOG_PRIORITY_BANDS = {
    "READY_TO_DECIDE",
    "ACTIVE_DEVELOPMENT",
    "BLOCKED",
    "INSUFFICIENT_EVIDENCE",
}
CATALOG_DISPOSITIONS = {
    "FINISH",
    "HOLD",
    "REWRITE",
    "GROUP",
    "RELEASE_PREP",
    "PITCH_PREP",
    "EXPERIMENT_NEXT",
    "NEED_MORE_EVIDENCE",
}

_VALUES_BY_KIND = {
    "READINESS": {"UNKNOWN", "IDEA", "DEVELOPING", "REVIEWABLE", "DELIVERY_READY"},
    "BLOCKER": {"UNKNOWN", "NONE", "MINOR", "MATERIAL", "HARD"},
    "ARTIST_INTENT": {"UNKNOWN", "ALIGNED", "MIXED", "CONFLICTING"},
    "IDENTITY_FIT": {"UNKNOWN", "ALIGNED", "EXPERIMENTAL", "CONFLICTING"},
    "CATALOG_RELATION": {"UNKNOWN", "DISTINCT", "COMPLEMENTARY", "OVERLAPPING"},
    "TARGET_FIT": {"UNKNOWN", "ALIGNED", "MIXED", "CONFLICTING"},
}
_ALLOWED_SOURCES_BY_KIND = {
    "READINESS": {"ARTIST_DECLARED", "OBSERVED", "INFERRED"},
    "BLOCKER": {"ARTIST_DECLARED", "OBSERVED", "VERIFIED_EXTERNAL", "INFERRED"},
    "ARTIST_INTENT": {"ARTIST_DECLARED", "OBSERVED", "INFERRED"},
    "IDENTITY_FIT": {"ARTIST_DECLARED", "OBSERVED", "INFERRED"},
    "CATALOG_RELATION": {"ARTIST_DECLARED", "OBSERVED", "INFERRED"},
    "TARGET_FIT": {"ARTIST_DECLARED", "OBSERVED", "VERIFIED_EXTERNAL", "INFERRED"},
}
_CORE_KINDS = ("READINESS", "BLOCKER", "ARTIST_INTENT")
_BAND_ORDER = (
    "READY_TO_DECIDE",
    "ACTIVE_DEVELOPMENT",
    "BLOCKED",
    "INSUFFICIENT_EVIDENCE",
)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    text = value.strip()
    if not text:
        raise ValidationError(f"{field} must not be empty")
    return text


def _enum_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    return value


@dataclass(frozen=True)
class CatalogEvidence:
    id: str
    song_id: str
    kind: str
    value: str
    source_kind: str
    source_ref: str
    freshness_state: str = "CURRENT"
    confidence: float = 1.0
    note: str | None = None

    def __post_init__(self) -> None:
        evidence_id = _required_text(self.id, "catalog evidence id")
        song_id = _required_text(self.song_id, "catalog evidence song_id")
        kind = _enum_text(self.kind, "catalog evidence kind")
        value = _enum_text(self.value, "catalog evidence value")
        source_kind = _enum_text(self.source_kind, "catalog evidence source")
        freshness_state = _enum_text(
            self.freshness_state, "catalog evidence freshness"
        )
        source_ref = _required_text(self.source_ref, "catalog evidence source_ref")

        if kind not in CATALOG_EVIDENCE_KINDS:
            raise ValidationError(f"unsupported catalog evidence kind: {kind}")
        if value not in _VALUES_BY_KIND[kind]:
            raise ValidationError(
                f"unsupported {kind} catalog evidence value: {value}"
            )
        if source_kind not in CATALOG_SOURCE_KINDS:
            raise ValidationError(f"unsupported catalog evidence source: {source_kind}")
        if source_kind not in _ALLOWED_SOURCES_BY_KIND[kind]:
            raise ValidationError(
                f"catalog evidence source {source_kind} cannot establish {kind}"
            )
        if freshness_state not in CATALOG_FRESHNESS_STATES:
            raise ValidationError(
                f"unsupported catalog evidence freshness: {freshness_state}"
            )
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, (int, float)
        ):
            raise ValidationError("catalog evidence confidence must be between 0 and 1")
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValidationError("catalog evidence confidence must be between 0 and 1")
        if self.note is not None and not isinstance(self.note, str):
            raise ValidationError("catalog evidence note must be text")
        note = None if self.note is None else self.note.strip() or None

        object.__setattr__(self, "id", evidence_id)
        object.__setattr__(self, "song_id", song_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "freshness_state", freshness_state)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "note", note)

    @property
    def actionable(self) -> bool:
        """Whether this row may support a current bounded catalog decision.

        INFERRED evidence remains visible but cannot, by itself, become decision truth.
        Freshness is supplied by the caller; this module never invents a TTL.
        """

        return self.freshness_state == "CURRENT" and self.source_kind != "INFERRED"

    @property
    def provisional(self) -> bool:
        return self.freshness_state == "CURRENT" and self.source_kind == "INFERRED"


@dataclass(frozen=True)
class CatalogSongCandidate:
    song_id: str
    evidence: tuple[CatalogEvidence, ...] = ()

    def __post_init__(self) -> None:
        song_id = _required_text(self.song_id, "catalog candidate song_id")
        try:
            rows = tuple(self.evidence)
        except TypeError as exc:
            raise ValidationError("catalog candidate evidence must be iterable") from exc
        if any(not isinstance(row, CatalogEvidence) for row in rows):
            raise ValidationError("catalog candidate evidence must be CatalogEvidence")
        ids = tuple(row.id for row in rows)
        if len(ids) != len(set(ids)):
            raise ValidationError("catalog evidence IDs must be unique within a Song")
        for row in rows:
            if row.song_id != song_id:
                raise ValidationError("catalog evidence belongs to a different Song")
        object.__setattr__(self, "song_id", song_id)
        object.__setattr__(self, "evidence", rows)


@dataclass(frozen=True)
class CatalogConflict:
    kind: str
    values: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class CatalogAssessment:
    song_id: str
    priority_band: str
    dispositions: tuple[str, ...]
    reason_codes: tuple[str, ...]
    conflicts: tuple[CatalogConflict, ...]
    unknown_kinds: tuple[str, ...]
    current_evidence_ids: tuple[str, ...]
    provisional_evidence_ids: tuple[str, ...]
    stale_or_unknown_evidence_ids: tuple[str, ...]
    smallest_next_step: str

    @property
    def external_action_authorized(self) -> bool:
        return False

    @property
    def release_authorized(self) -> bool:
        return False

    @property
    def pitch_authorized(self) -> bool:
        return False


@dataclass(frozen=True)
class CatalogPriorityGroup:
    priority_band: str
    song_ids: tuple[str, ...]
    semantically_tied: bool
    ordering_note: str


@dataclass(frozen=True)
class CatalogTopology:
    assessments: tuple[CatalogAssessment, ...]
    groups: tuple[CatalogPriorityGroup, ...]

    @property
    def external_action_authorized(self) -> bool:
        return False

    @property
    def predictive_hit_score_available(self) -> bool:
        return False


def _rows(candidate: CatalogSongCandidate, kind: str) -> tuple[CatalogEvidence, ...]:
    return tuple(row for row in candidate.evidence if row.kind == kind)


def _conflict_for(
    kind: str,
    rows: tuple[CatalogEvidence, ...],
) -> CatalogConflict | None:
    actionable = tuple(row for row in rows if row.actionable)
    values = tuple(sorted({row.value for row in actionable}))
    if len(values) <= 1:
        return None
    return CatalogConflict(
        kind=kind,
        values=values,
        evidence_ids=tuple(sorted(row.id for row in actionable)),
    )


def _resolved_value(
    candidate: CatalogSongCandidate,
    kind: str,
) -> tuple[str | None, CatalogConflict | None]:
    rows = _rows(candidate, kind)
    conflict = _conflict_for(kind, rows)
    if conflict is not None:
        return None, conflict
    actionable = tuple(row for row in rows if row.actionable)
    if not actionable:
        return None, None
    return actionable[0].value, None


def _evidence_buckets(
    candidate: CatalogSongCandidate,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    current = tuple(sorted(row.id for row in candidate.evidence if row.actionable))
    provisional = tuple(sorted(row.id for row in candidate.evidence if row.provisional))
    stale_or_unknown = tuple(
        sorted(
            row.id
            for row in candidate.evidence
            if row.freshness_state in {"STALE", "UNKNOWN"}
        )
    )
    return current, provisional, stale_or_unknown


def _missing_step(kind: str) -> str:
    label = kind.lower().replace("_", " ")
    return (
        f"Record current, source-bound {label} evidence for this exact Song before "
        "treating it as catalog decision truth."
    )


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def assess_catalog_song(candidate: CatalogSongCandidate) -> CatalogAssessment:
    if not isinstance(candidate, CatalogSongCandidate):
        raise ValidationError("catalog assessment requires a CatalogSongCandidate")

    resolved: dict[str, str | None] = {}
    conflicts: list[CatalogConflict] = []
    for kind in CATALOG_EVIDENCE_KINDS:
        value, conflict = _resolved_value(candidate, kind)
        resolved[kind] = value
        if conflict is not None:
            conflicts.append(conflict)
    conflicts.sort(key=lambda item: item.kind)

    current, provisional, stale_or_unknown = _evidence_buckets(candidate)
    unknown = tuple(kind for kind in _CORE_KINDS if resolved[kind] is None)

    if conflicts:
        first = conflicts[0]
        return CatalogAssessment(
            song_id=candidate.song_id,
            priority_band="INSUFFICIENT_EVIDENCE",
            dispositions=("NEED_MORE_EVIDENCE",),
            reason_codes=("CONFLICTING_CURRENT_EVIDENCE",),
            conflicts=tuple(conflicts),
            unknown_kinds=unknown,
            current_evidence_ids=current,
            provisional_evidence_ids=provisional,
            stale_or_unknown_evidence_ids=stale_or_unknown,
            smallest_next_step=(
                f"Review the conflicting {first.kind.lower().replace('_', ' ')} evidence "
                "for this exact Song and record an explicit correction or newer observation."
            ),
        )

    if unknown:
        return CatalogAssessment(
            song_id=candidate.song_id,
            priority_band="INSUFFICIENT_EVIDENCE",
            dispositions=("NEED_MORE_EVIDENCE",),
            reason_codes=("MISSING_CURRENT_CORE_EVIDENCE",),
            conflicts=(),
            unknown_kinds=unknown,
            current_evidence_ids=current,
            provisional_evidence_ids=provisional,
            stale_or_unknown_evidence_ids=stale_or_unknown,
            smallest_next_step=_missing_step(unknown[0]),
        )

    readiness = resolved["READINESS"]
    blocker = resolved["BLOCKER"]
    intent = resolved["ARTIST_INTENT"]
    identity = resolved["IDENTITY_FIT"]
    relation = resolved["CATALOG_RELATION"]
    target_fit = resolved["TARGET_FIT"]

    if readiness == "UNKNOWN" or blocker == "UNKNOWN" or intent == "UNKNOWN":
        unresolved_kind = next(
            kind for kind in _CORE_KINDS if resolved[kind] == "UNKNOWN"
        )
        return CatalogAssessment(
            song_id=candidate.song_id,
            priority_band="INSUFFICIENT_EVIDENCE",
            dispositions=("NEED_MORE_EVIDENCE",),
            reason_codes=("EXPLICITLY_UNKNOWN_CORE_EVIDENCE",),
            conflicts=(),
            unknown_kinds=(unresolved_kind,),
            current_evidence_ids=current,
            provisional_evidence_ids=provisional,
            stale_or_unknown_evidence_ids=stale_or_unknown,
            smallest_next_step=_missing_step(unresolved_kind),
        )

    if blocker in {"MATERIAL", "HARD"}:
        severity = str(blocker)
        return CatalogAssessment(
            song_id=candidate.song_id,
            priority_band="BLOCKED",
            dispositions=("HOLD",),
            reason_codes=(f"{severity}_BLOCKER", "OPTION_VALUE_PRESERVED"),
            conflicts=(),
            unknown_kinds=(),
            current_evidence_ids=current,
            provisional_evidence_ids=provisional,
            stale_or_unknown_evidence_ids=stale_or_unknown,
            smallest_next_step=(
                "Resolve or revalidate one material catalog blocker for this exact Song; "
                "HOLD preserves the Song rather than rejecting it."
            ),
        )

    reasons: list[str] = []
    dispositions: list[str] = []
    band = "ACTIVE_DEVELOPMENT"

    if blocker == "MINOR":
        reasons.append("MINOR_BLOCKER_PRESENT")

    if readiness in {"IDEA", "DEVELOPING"}:
        reasons.append(f"READINESS_{readiness}")
        if intent == "CONFLICTING":
            dispositions.append("REWRITE")
            reasons.append("ARTIST_INTENT_CONFLICT")
        else:
            if identity == "EXPERIMENTAL":
                dispositions.append("EXPERIMENT_NEXT")
                reasons.append("IDENTITY_EXPERIMENT")
            dispositions.append("FINISH")

    elif readiness == "REVIEWABLE":
        reasons.append("READINESS_REVIEWABLE")
        if intent == "CONFLICTING":
            dispositions.append("REWRITE")
            reasons.append("ARTIST_INTENT_CONFLICT")
        else:
            dispositions.append("FINISH")
            if relation == "COMPLEMENTARY":
                dispositions.append("GROUP")
                reasons.append("CATALOG_COMPLEMENT")

    elif readiness == "DELIVERY_READY":
        reasons.append("READINESS_DELIVERY_READY")
        if intent == "ALIGNED":
            band = "READY_TO_DECIDE"
            dispositions.append("RELEASE_PREP")
            reasons.append("ARTIST_INTENT_ALIGNED")
            if target_fit == "ALIGNED":
                dispositions.append("PITCH_PREP")
                reasons.append("TARGET_FIT_ALIGNED")
            if relation == "COMPLEMENTARY":
                dispositions.append("GROUP")
                reasons.append("CATALOG_COMPLEMENT")
            if identity == "EXPERIMENTAL":
                dispositions.append("EXPERIMENT_NEXT")
                reasons.append("IDENTITY_EXPERIMENT_OPTION")
        elif intent == "MIXED":
            dispositions.extend(("HOLD", "FINISH"))
            reasons.extend(("ARTIST_INTENT_MIXED", "OPTION_VALUE_PRESERVED"))
        else:
            dispositions.extend(("REWRITE", "HOLD"))
            reasons.extend(("ARTIST_INTENT_CONFLICT", "OPTION_VALUE_PRESERVED"))
    else:
        raise ValidationError(f"unsupported resolved readiness: {readiness}")

    dispositions_tuple = _dedupe(dispositions)
    if band == "READY_TO_DECIDE":
        next_step = (
            "Ask the Artist to review the bounded preparation options for this exact Song "
            "and record the decision; no release, pitch, send or provider action is authorized."
        )
    elif "REWRITE" in dispositions_tuple:
        next_step = (
            "Create one bounded rewrite or alternate Version that tests the stated intent gap, "
            "then record fresh readiness evidence before reprioritizing."
        )
    else:
        next_step = (
            "Produce one bounded reviewable next artifact or Version, then record fresh "
            "readiness and Artist-intent evidence before reprioritizing."
        )

    return CatalogAssessment(
        song_id=candidate.song_id,
        priority_band=band,
        dispositions=dispositions_tuple,
        reason_codes=_dedupe(reasons),
        conflicts=(),
        unknown_kinds=(),
        current_evidence_ids=current,
        provisional_evidence_ids=provisional,
        stale_or_unknown_evidence_ids=stale_or_unknown,
        smallest_next_step=next_step,
    )


def prioritize_catalog(
    candidates: Iterable[CatalogSongCandidate],
) -> CatalogTopology:
    try:
        rows = tuple(candidates)
    except TypeError as exc:
        raise ValidationError("catalog prioritization requires iterable candidates") from exc
    if not rows:
        raise ValidationError("catalog prioritization needs at least one Song candidate")
    if any(not isinstance(row, CatalogSongCandidate) for row in rows):
        raise ValidationError("catalog prioritization requires CatalogSongCandidate rows")

    song_ids = tuple(row.song_id for row in rows)
    if len(song_ids) != len(set(song_ids)):
        raise ValidationError("catalog candidate Song IDs must be unique")

    evidence_ids = tuple(item.id for row in rows for item in row.evidence)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValidationError("catalog evidence IDs must be globally unique")

    assessments = tuple(assess_catalog_song(row) for row in rows)
    groups: list[CatalogPriorityGroup] = []
    for band in _BAND_ORDER:
        grouped_ids = tuple(
            sorted(item.song_id for item in assessments if item.priority_band == band)
        )
        if not grouped_ids:
            continue
        groups.append(
            CatalogPriorityGroup(
                priority_band=band,
                song_ids=grouped_ids,
                semantically_tied=len(grouped_ids) > 1,
                ordering_note=(
                    "Song IDs are sorted only for deterministic presentation; Songs within "
                    "the same qualitative band are not given a predictive or artistic rank."
                ),
            )
        )

    return CatalogTopology(
        assessments=tuple(sorted(assessments, key=lambda item: item.song_id)),
        groups=tuple(groups),
    )
