from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .lineage import ValidationError

RESILIENCE_WORK_KINDS = {
    "PROFESSIONAL_SERVICE",
    "STUDIO_SERVICE",
    "REMOTE_SESSION",
    "LIVE_SERVICE",
    "TRAVEL",
}
DEPENDENCY_KINDS = {
    "EQUIPMENT",
    "FACILITY",
    "VENDOR",
    "VENUE",
    "TRANSPORT",
    "PERSON",
    "INSURANCE_RECORD",
}
AVAILABILITY_STATES = {"AVAILABLE", "DEGRADED", "UNAVAILABLE", "UNKNOWN"}
EVIDENCE_SOURCE_KINDS = {
    "USER_DECLARED",
    "OBSERVED",
    "VERIFIED_EXTERNAL",
    "INFERRED",
}
VERIFIED_FAVORABLE_SOURCES = {"OBSERVED", "VERIFIED_EXTERNAL"}
EVIDENCE_STATES = {
    "CURRENT",
    "REVALIDATION_REQUIRED",
    "FUTURE",
    "UNVERIFIED_AVAILABLE",
}
CAPACITY_STATES = {"UNKNOWN", "WITHIN_CAPACITY", "AT_CAPACITY", "OVER_CAPACITY"}
DISRUPTION_KINDS = {
    "EQUIPMENT_FAILURE",
    "FACILITY_DISRUPTION",
    "VENDOR_OUTAGE",
    "VENUE_DISRUPTION",
    "TRANSPORT_DISRUPTION",
    "PERSON_UNAVAILABLE",
    "CANCELLATION",
    "OVERLOAD",
    "OTHER",
}
DISRUPTION_STATES = {"ACTIVE", "RESOLVED", "UNKNOWN"}
RECOVERY_STEP_STATES = {"PENDING", "READY", "BLOCKED", "COMPLETE"}
RESILIENCE_DISPOSITIONS = {
    "STABLE",
    "NEEDS_EVIDENCE",
    "AT_RISK",
    "DISRUPTED",
    "RECOVERY_BLOCKED",
}


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be text")
    value = " ".join(value.split())
    if not value:
        raise ValidationError(f"{field_name} must not be empty")
    return value


def _enum(value: object, field_name: str, allowed: set[str]) -> str:
    value = _text(value, field_name).upper()
    if value not in allowed:
        raise ValidationError(f"unsupported {field_name}: {value}")
    return value


def _aware(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be timezone-aware")
    return value


def _text_tuple(
    values: object,
    field_name: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ValidationError(f"{field_name} must be a tuple")
    result = tuple(_text(value, field_name) for value in values)
    if not allow_empty and not result:
        raise ValidationError(f"{field_name} must not be empty")
    if len(result) != len(set(result)):
        raise ValidationError(f"{field_name} must not contain duplicates")
    return result


def _typed_tuple(values: object, field_name: str, item_type: type) -> tuple:
    if not isinstance(values, tuple):
        raise ValidationError(f"{field_name} must be a tuple")
    if any(not isinstance(value, item_type) for value in values):
        raise ValidationError(
            f"{field_name} must contain only {item_type.__name__} values"
        )
    return values


@dataclass(frozen=True)
class ServiceResilienceContext:
    role: str
    work_kind: str
    service_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _text(self.role, "role"))
        object.__setattr__(
            self,
            "work_kind",
            _enum(self.work_kind, "work kind", RESILIENCE_WORK_KINDS),
        )
        object.__setattr__(
            self, "service_ref", _text(self.service_ref, "service_ref")
        )


@dataclass(frozen=True)
class EvidenceBinding:
    source_kind: str
    source_ref: str
    observed_at: datetime
    revalidate_after: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_kind",
            _enum(self.source_kind, "evidence source", EVIDENCE_SOURCE_KINDS),
        )
        object.__setattr__(
            self, "source_ref", _text(self.source_ref, "source_ref")
        )
        observed = _aware(self.observed_at, "observed_at")
        revalidate = _aware(self.revalidate_after, "revalidate_after")
        if revalidate <= observed:
            raise ValidationError("revalidate_after must be after observed_at")


@dataclass(frozen=True)
class CapacitySnapshot:
    state: str
    commitment_refs: tuple[str, ...]
    at_risk_commitment_refs: tuple[str, ...]
    evidence: EvidenceBinding

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "state", _enum(self.state, "capacity state", CAPACITY_STATES)
        )
        commitments = _text_tuple(
            self.commitment_refs, "commitment_refs", allow_empty=True
        )
        at_risk = _text_tuple(
            self.at_risk_commitment_refs,
            "at_risk_commitment_refs",
            allow_empty=True,
        )
        if any(ref not in commitments for ref in at_risk):
            raise ValidationError(
                "at_risk_commitment_refs must be contained in commitment_refs"
            )
        if self.state == "OVER_CAPACITY" and not at_risk:
            raise ValidationError(
                "OVER_CAPACITY requires at least one at-risk commitment"
            )
        if not isinstance(self.evidence, EvidenceBinding):
            raise ValidationError("capacity evidence must be EvidenceBinding")
        object.__setattr__(self, "commitment_refs", commitments)
        object.__setattr__(self, "at_risk_commitment_refs", at_risk)


@dataclass(frozen=True)
class FallbackOption:
    id: str
    label: str
    availability: str
    evidence: EvidenceBinding

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "fallback id"))
        object.__setattr__(self, "label", _text(self.label, "fallback label"))
        object.__setattr__(
            self,
            "availability",
            _enum(
                self.availability,
                "fallback availability",
                AVAILABILITY_STATES,
            ),
        )
        if not isinstance(self.evidence, EvidenceBinding):
            raise ValidationError("fallback evidence must be EvidenceBinding")


@dataclass(frozen=True)
class ResilienceDependency:
    id: str
    kind: str
    label: str
    critical: bool
    availability: str
    commitment_refs: tuple[str, ...]
    evidence: EvidenceBinding
    fallbacks: tuple[FallbackOption, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "dependency id"))
        object.__setattr__(
            self, "kind", _enum(self.kind, "dependency kind", DEPENDENCY_KINDS)
        )
        object.__setattr__(self, "label", _text(self.label, "dependency label"))
        if not isinstance(self.critical, bool):
            raise ValidationError("critical must be boolean")
        object.__setattr__(
            self,
            "availability",
            _enum(
                self.availability,
                "dependency availability",
                AVAILABILITY_STATES,
            ),
        )
        object.__setattr__(
            self,
            "commitment_refs",
            _text_tuple(
                self.commitment_refs, "dependency commitment_refs", allow_empty=True
            ),
        )
        if not isinstance(self.evidence, EvidenceBinding):
            raise ValidationError("dependency evidence must be EvidenceBinding")
        fallbacks = _typed_tuple(self.fallbacks, "fallbacks", FallbackOption)
        ids = tuple(item.id for item in fallbacks)
        if len(ids) != len(set(ids)):
            raise ValidationError("fallback ids must be unique within a dependency")
        object.__setattr__(self, "fallbacks", fallbacks)


@dataclass(frozen=True)
class DisruptionEvent:
    id: str
    kind: str
    state: str
    statement: str
    affected_commitment_refs: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    source_kind: str
    source_ref: str
    observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "disruption id"))
        object.__setattr__(
            self, "kind", _enum(self.kind, "disruption kind", DISRUPTION_KINDS)
        )
        object.__setattr__(
            self, "state", _enum(self.state, "disruption state", DISRUPTION_STATES)
        )
        object.__setattr__(
            self, "statement", _text(self.statement, "disruption statement")
        )
        object.__setattr__(
            self,
            "affected_commitment_refs",
            _text_tuple(
                self.affected_commitment_refs,
                "affected_commitment_refs",
                allow_empty=False,
            ),
        )
        object.__setattr__(
            self,
            "dependency_ids",
            _text_tuple(self.dependency_ids, "dependency_ids", allow_empty=True),
        )
        object.__setattr__(
            self,
            "source_kind",
            _enum(self.source_kind, "disruption source", EVIDENCE_SOURCE_KINDS),
        )
        object.__setattr__(
            self, "source_ref", _text(self.source_ref, "disruption source_ref")
        )
        _aware(self.observed_at, "disruption observed_at")


@dataclass(frozen=True)
class RecoveryStep:
    id: str
    owner: str
    action: str
    state: str
    applies_to_disruption_ids: tuple[str, ...]
    prerequisite_step_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "recovery step id"))
        object.__setattr__(self, "owner", _text(self.owner, "recovery owner"))
        object.__setattr__(self, "action", _text(self.action, "recovery action"))
        object.__setattr__(
            self,
            "state",
            _enum(self.state, "recovery step state", RECOVERY_STEP_STATES),
        )
        object.__setattr__(
            self,
            "applies_to_disruption_ids",
            _text_tuple(
                self.applies_to_disruption_ids,
                "applies_to_disruption_ids",
                allow_empty=False,
            ),
        )
        prerequisites = _text_tuple(
            self.prerequisite_step_ids,
            "prerequisite_step_ids",
            allow_empty=True,
        )
        if self.id in prerequisites:
            raise ValidationError("recovery step cannot depend on itself")
        object.__setattr__(self, "prerequisite_step_ids", prerequisites)


@dataclass(frozen=True)
class CommunicationNeed:
    id: str
    commitment_ref: str
    audience: str
    owner: str
    channel: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "communication need id"))
        object.__setattr__(
            self,
            "commitment_ref",
            _text(self.commitment_ref, "communication commitment_ref"),
        )
        object.__setattr__(self, "audience", _text(self.audience, "communication audience"))
        object.__setattr__(self, "owner", _text(self.owner, "communication owner"))
        object.__setattr__(self, "channel", _text(self.channel, "communication channel"))
        object.__setattr__(self, "reason", _text(self.reason, "communication reason"))


@dataclass(frozen=True)
class DependencyAssessment:
    dependency_id: str
    conservative_state: str
    evidence_state: str
    viable_fallback_ids: tuple[str, ...]
    unverified_fallback_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]

    @property
    def continuity_proven(self) -> bool:
        return (
            self.conservative_state == "AVAILABLE"
            or bool(self.viable_fallback_ids)
        )


@dataclass(frozen=True)
class ServiceResilienceAssessment:
    disposition: str
    context: ServiceResilienceContext
    capacity_state: str
    dependency_assessments: tuple[DependencyAssessment, ...]
    active_disruption_ids: tuple[str, ...]
    affected_commitment_refs: tuple[str, ...]
    communication_need_ids: tuple[str, ...]
    recovery_blocker_step_ids: tuple[str, ...]
    gap_codes: tuple[str, ...]
    mutation_authorized: bool = field(default=False, init=False)
    messaging_authorized: bool = field(default=False, init=False)
    scheduling_authorized: bool = field(default=False, init=False)
    cancellation_authorized: bool = field(default=False, init=False)
    refund_authorized: bool = field(default=False, init=False)
    purchase_authorized: bool = field(default=False, init=False)
    spend_authorized: bool = field(default=False, init=False)
    contract_authorized: bool = field(default=False, init=False)
    insurance_coverage_certified: bool = field(default=False, init=False)
    legal_sufficiency_certified: bool = field(default=False, init=False)
    provider_action_authorized: bool = field(default=False, init=False)
    daw_action_authorized: bool = field(default=False, init=False)
    external_action_authorized: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.disposition not in RESILIENCE_DISPOSITIONS:
            raise ValidationError(
                f"unsupported resilience disposition: {self.disposition}"
            )

    @property
    def grants_any_authority(self) -> bool:
        return False


def _evidence_state(binding: EvidenceBinding, as_of: datetime) -> str:
    if binding.observed_at > as_of:
        return "FUTURE"
    if binding.revalidate_after <= as_of:
        return "REVALIDATION_REQUIRED"
    return "CURRENT"


def _conservative_availability(
    availability: str,
    evidence: EvidenceBinding,
    as_of: datetime,
) -> tuple[str, str, tuple[str, ...]]:
    evidence_state = _evidence_state(evidence, as_of)
    if evidence_state != "CURRENT":
        return "UNKNOWN", evidence_state, (evidence_state,)
    if evidence.source_kind == "INFERRED":
        return "UNKNOWN", "UNVERIFIED", ("DEPENDENCY_INFERRED_UNVERIFIED",)
    if availability == "AVAILABLE":
        if evidence.source_kind not in VERIFIED_FAVORABLE_SOURCES:
            return (
                "UNKNOWN",
                "UNVERIFIED_AVAILABLE",
                ("FAVORABLE_AVAILABILITY_UNVERIFIED",),
            )
        return "AVAILABLE", "CURRENT", ()
    if availability == "DEGRADED":
        return "DEGRADED", "CURRENT", ("DEPENDENCY_DEGRADED",)
    if availability == "UNAVAILABLE":
        return "UNAVAILABLE", "CURRENT", ("DEPENDENCY_UNAVAILABLE",)
    return "UNKNOWN", "CURRENT", ("DEPENDENCY_UNKNOWN",)


def _validate_unique(items: tuple, field_name: str) -> None:
    ids = tuple(item.id for item in items)
    if len(ids) != len(set(ids)):
        raise ValidationError(f"{field_name} ids must be unique")


def _validate_recovery_graph(steps: tuple[RecoveryStep, ...]) -> None:
    by_id = {step.id: step for step in steps}
    for step in steps:
        unknown = [value for value in step.prerequisite_step_ids if value not in by_id]
        if unknown:
            raise ValidationError(
                "recovery step references unknown prerequisite ids: "
                + ", ".join(unknown)
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visited:
            return
        if step_id in visiting:
            raise ValidationError("recovery step prerequisites must be acyclic")
        visiting.add(step_id)
        for prerequisite in by_id[step_id].prerequisite_step_ids:
            visit(prerequisite)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in by_id:
        visit(step_id)


def assess_service_resilience(
    context: ServiceResilienceContext,
    *,
    as_of: datetime,
    capacity: CapacitySnapshot,
    dependencies: tuple[ResilienceDependency, ...] = (),
    disruptions: tuple[DisruptionEvent, ...] = (),
    recovery_steps: tuple[RecoveryStep, ...] = (),
    communication_needs: tuple[CommunicationNeed, ...] = (),
) -> ServiceResilienceAssessment:
    if not isinstance(context, ServiceResilienceContext):
        raise ValidationError("context must be ServiceResilienceContext")
    as_of = _aware(as_of, "as_of")
    if not isinstance(capacity, CapacitySnapshot):
        raise ValidationError("capacity must be CapacitySnapshot")
    dependencies = _typed_tuple(dependencies, "dependencies", ResilienceDependency)
    disruptions = _typed_tuple(disruptions, "disruptions", DisruptionEvent)
    recovery_steps = _typed_tuple(recovery_steps, "recovery_steps", RecoveryStep)
    communication_needs = _typed_tuple(
        communication_needs, "communication_needs", CommunicationNeed
    )
    _validate_unique(dependencies, "dependency")
    _validate_unique(disruptions, "disruption")
    _validate_unique(recovery_steps, "recovery step")
    _validate_unique(communication_needs, "communication need")
    _validate_recovery_graph(recovery_steps)

    dependency_ids = {item.id for item in dependencies}
    for event in disruptions:
        unknown = [value for value in event.dependency_ids if value not in dependency_ids]
        if unknown:
            raise ValidationError(
                "disruption references unknown dependency ids: " + ", ".join(unknown)
            )

    disruption_ids = {item.id for item in disruptions}
    for step in recovery_steps:
        unknown = [
            value
            for value in step.applies_to_disruption_ids
            if value not in disruption_ids
        ]
        if unknown:
            raise ValidationError(
                "recovery step references unknown disruption ids: "
                + ", ".join(unknown)
            )

    capacity_evidence_state = _evidence_state(capacity.evidence, as_of)
    capacity_state = (
        capacity.state if capacity_evidence_state == "CURRENT" else "UNKNOWN"
    )
    gaps: list[str] = []
    if capacity_evidence_state != "CURRENT":
        gaps.append(f"CAPACITY_{capacity_evidence_state}")

    assessments: list[DependencyAssessment] = []
    affected: list[str] = []
    if capacity_state == "OVER_CAPACITY":
        affected.extend(capacity.at_risk_commitment_refs)
        gaps.append("OVER_CAPACITY")

    for dependency in dependencies:
        conservative_state, evidence_state, reasons = _conservative_availability(
            dependency.availability, dependency.evidence, as_of
        )
        viable: list[str] = []
        unverified: list[str] = []
        for fallback in dependency.fallbacks:
            fallback_state, fallback_evidence_state, _ = _conservative_availability(
                fallback.availability, fallback.evidence, as_of
            )
            if fallback_state == "AVAILABLE":
                viable.append(fallback.id)
            elif (
                fallback.availability == "AVAILABLE"
                and fallback_evidence_state == "UNVERIFIED_AVAILABLE"
            ):
                unverified.append(fallback.id)

        reason_codes = list(reasons)
        if unverified:
            reason_codes.append("FALLBACK_AVAILABLE_ONLY_BY_UNVERIFIED_CLAIM")
        if dependency.critical:
            if conservative_state in {"UNAVAILABLE", "DEGRADED"}:
                affected.extend(dependency.commitment_refs)
                if not viable:
                    gaps.append(
                        f"CRITICAL_DEPENDENCY_{conservative_state}:{dependency.id}"
                    )
                else:
                    gaps.append(f"CRITICAL_DEPENDENCY_ON_FALLBACK:{dependency.id}")
            elif conservative_state == "UNKNOWN":
                if not viable:
                    gaps.append(f"CRITICAL_DEPENDENCY_NEEDS_EVIDENCE:{dependency.id}")
            if unverified and not viable:
                gaps.append(f"CRITICAL_FALLBACK_UNVERIFIED:{dependency.id}")
        assessments.append(
            DependencyAssessment(
                dependency_id=dependency.id,
                conservative_state=conservative_state,
                evidence_state=evidence_state,
                viable_fallback_ids=tuple(viable),
                unverified_fallback_ids=tuple(unverified),
                reason_codes=tuple(dict.fromkeys(reason_codes)),
            )
        )

    for event in disruptions:
        if event.observed_at > as_of:
            gaps.append(f"DISRUPTION_EVIDENCE_FUTURE:{event.id}")
        elif event.state == "UNKNOWN":
            gaps.append(f"DISRUPTION_STATE_UNKNOWN:{event.id}")

    active_disruptions = tuple(
        item
        for item in disruptions
        if item.state == "ACTIVE" and item.observed_at <= as_of
    )
    for event in active_disruptions:
        affected.extend(event.affected_commitment_refs)

    affected_refs = tuple(dict.fromkeys(affected))
    communication_by_commitment = {
        need.commitment_ref: need for need in communication_needs
    }
    for commitment_ref in affected_refs:
        if commitment_ref not in communication_by_commitment:
            gaps.append(f"COMMUNICATION_OWNER_MISSING:{commitment_ref}")

    step_by_id = {step.id: step for step in recovery_steps}
    blockers: list[str] = []
    active_ids = {item.id for item in active_disruptions}
    applicable_steps = tuple(
        step
        for step in recovery_steps
        if active_ids & set(step.applies_to_disruption_ids)
    )
    if active_disruptions and not applicable_steps:
        gaps.append("RECOVERY_PLAN_MISSING")
    for step in applicable_steps:
        unmet = [
            prereq
            for prereq in step.prerequisite_step_ids
            if step_by_id[prereq].state != "COMPLETE"
        ]
        if step.state == "BLOCKED" or (
            step.state in {"READY", "COMPLETE"} and unmet
        ):
            blockers.append(step.id)
        elif step.state == "PENDING" and unmet:
            blockers.append(step.id)
    if blockers:
        gaps.append("RECOVERY_PREREQUISITES_BLOCKED")

    if blockers or (active_disruptions and not applicable_steps):
        disposition = "RECOVERY_BLOCKED"
    elif active_disruptions:
        disposition = "DISRUPTED"
    elif capacity_state in {"AT_CAPACITY", "OVER_CAPACITY"} or any(
        assessment.conservative_state in {"DEGRADED", "UNAVAILABLE"}
        for assessment in assessments
        if next(
            dependency.critical
            for dependency in dependencies
            if dependency.id == assessment.dependency_id
        )
    ):
        disposition = "AT_RISK"
    elif any(
        code.startswith("CAPACITY_")
        or "NEEDS_EVIDENCE" in code
        or "UNVERIFIED" in code
        or "EVIDENCE_FUTURE" in code
        or "STATE_UNKNOWN" in code
        for code in gaps
    ):
        disposition = "NEEDS_EVIDENCE"
    else:
        disposition = "STABLE"

    return ServiceResilienceAssessment(
        disposition=disposition,
        context=context,
        capacity_state=capacity_state,
        dependency_assessments=tuple(assessments),
        active_disruption_ids=tuple(item.id for item in active_disruptions),
        affected_commitment_refs=affected_refs,
        communication_need_ids=tuple(item.id for item in communication_needs),
        recovery_blocker_step_ids=tuple(dict.fromkeys(blockers)),
        gap_codes=tuple(dict.fromkeys(gaps)),
    )
