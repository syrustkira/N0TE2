from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from n0te2.lineage import ValidationError
from n0te2.service_resilience import (
    CapacitySnapshot,
    CommunicationNeed,
    DisruptionEvent,
    EvidenceBinding,
    FallbackOption,
    RecoveryStep,
    ResilienceDependency,
    ServiceResilienceAssessment,
    ServiceResilienceContext,
    assess_service_resilience,
)


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def evidence(
    source_kind: str = "OBSERVED",
    *,
    source_ref: str = "evidence:test",
    observed_delta: timedelta = timedelta(hours=-1),
    valid_for: timedelta = timedelta(days=1),
) -> EvidenceBinding:
    observed = NOW + observed_delta
    return EvidenceBinding(
        source_kind=source_kind,
        source_ref=source_ref,
        observed_at=observed,
        revalidate_after=observed + valid_for,
    )


def context(
    work_kind: str = "STUDIO_SERVICE",
    *,
    role: str = "Mix Engineer",
) -> ServiceResilienceContext:
    return ServiceResilienceContext(
        role=role,
        work_kind=work_kind,
        service_ref="service:mix-session",
    )


def capacity(
    state: str = "WITHIN_CAPACITY",
    *,
    commitments: tuple[str, ...] = ("job:mix-1",),
    at_risk: tuple[str, ...] = (),
    binding: EvidenceBinding | None = None,
) -> CapacitySnapshot:
    return CapacitySnapshot(
        state=state,
        commitment_refs=commitments,
        at_risk_commitment_refs=at_risk,
        evidence=binding or evidence("USER_DECLARED", source_ref="capacity:artist"),
    )


def dependency(
    *,
    dependency_id: str = "dep:interface",
    kind: str = "EQUIPMENT",
    critical: bool = True,
    availability: str = "AVAILABLE",
    binding: EvidenceBinding | None = None,
    commitments: tuple[str, ...] = ("job:mix-1",),
    fallbacks: tuple[FallbackOption, ...] = (),
) -> ResilienceDependency:
    return ResilienceDependency(
        id=dependency_id,
        kind=kind,
        label="Primary interface",
        critical=critical,
        availability=availability,
        commitment_refs=commitments,
        evidence=binding or evidence(),
        fallbacks=fallbacks,
    )


class ServiceResilienceTests(unittest.TestCase):
    def test_verified_current_studio_dependency_can_be_stable(self) -> None:
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=capacity(),
            dependencies=(dependency(),),
        )
        self.assertEqual(result.disposition, "STABLE")
        self.assertEqual(result.capacity_state, "WITHIN_CAPACITY")
        self.assertEqual(result.gap_codes, ())
        self.assertTrue(result.dependency_assessments[0].continuity_proven)

    def test_favorable_user_declaration_does_not_prove_critical_availability(self) -> None:
        item = dependency(binding=evidence("USER_DECLARED"))
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=capacity(),
            dependencies=(item,),
        )
        self.assertEqual(result.disposition, "NEEDS_EVIDENCE")
        assessment = result.dependency_assessments[0]
        self.assertEqual(assessment.conservative_state, "UNKNOWN")
        self.assertEqual(assessment.evidence_state, "UNVERIFIED_AVAILABLE")
        self.assertIn(
            "CRITICAL_DEPENDENCY_NEEDS_EVIDENCE:dep:interface",
            result.gap_codes,
        )

    def test_adverse_user_declaration_is_respected_conservatively(self) -> None:
        item = dependency(
            availability="UNAVAILABLE",
            binding=evidence("USER_DECLARED"),
        )
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=capacity(),
            dependencies=(item,),
        )
        self.assertEqual(result.disposition, "AT_RISK")
        self.assertEqual(
            result.dependency_assessments[0].conservative_state,
            "UNAVAILABLE",
        )
        self.assertIn("job:mix-1", result.affected_commitment_refs)
        self.assertIn(
            "CRITICAL_DEPENDENCY_UNAVAILABLE:dep:interface",
            result.gap_codes,
        )

    def test_verified_fallback_preserves_continuity_but_does_not_erase_risk(self) -> None:
        fallback = FallbackOption(
            id="fallback:spare-interface",
            label="Spare interface",
            availability="AVAILABLE",
            evidence=evidence("OBSERVED", source_ref="inventory:spare"),
        )
        item = dependency(
            availability="UNAVAILABLE",
            binding=evidence("USER_DECLARED"),
            fallbacks=(fallback,),
        )
        comm = CommunicationNeed(
            id="comm:mix-client",
            commitment_ref="job:mix-1",
            audience="mix client",
            owner="Mix Engineer",
            channel="email",
            reason="Primary interface unavailable; service is running on fallback.",
        )
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=capacity(),
            dependencies=(item,),
            communication_needs=(comm,),
        )
        assessment = result.dependency_assessments[0]
        self.assertEqual(result.disposition, "AT_RISK")
        self.assertEqual(
            assessment.viable_fallback_ids,
            ("fallback:spare-interface",),
        )
        self.assertTrue(assessment.continuity_proven)
        self.assertIn(
            "CRITICAL_DEPENDENCY_ON_FALLBACK:dep:interface",
            result.gap_codes,
        )
        self.assertNotIn(
            "COMMUNICATION_OWNER_MISSING:job:mix-1",
            result.gap_codes,
        )

    def test_declared_fallback_does_not_close_critical_gap(self) -> None:
        fallback = FallbackOption(
            id="fallback:friend-rig",
            label="Friend's rig",
            availability="AVAILABLE",
            evidence=evidence("USER_DECLARED", source_ref="note:friend-rig"),
        )
        item = dependency(
            availability="UNAVAILABLE",
            binding=evidence("OBSERVED"),
            fallbacks=(fallback,),
        )
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=capacity(),
            dependencies=(item,),
        )
        assessment = result.dependency_assessments[0]
        self.assertEqual(assessment.viable_fallback_ids, ())
        self.assertEqual(
            assessment.unverified_fallback_ids,
            ("fallback:friend-rig",),
        )
        self.assertIn(
            "CRITICAL_FALLBACK_UNVERIFIED:dep:interface",
            result.gap_codes,
        )

    def test_stale_favorable_dependency_requires_revalidation(self) -> None:
        stale = evidence(
            "VERIFIED_EXTERNAL",
            observed_delta=timedelta(days=-4),
            valid_for=timedelta(days=1),
        )
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=capacity(),
            dependencies=(dependency(binding=stale),),
        )
        assessment = result.dependency_assessments[0]
        self.assertEqual(result.disposition, "NEEDS_EVIDENCE")
        self.assertEqual(assessment.evidence_state, "REVALIDATION_REQUIRED")
        self.assertEqual(assessment.conservative_state, "UNKNOWN")

    def test_inferred_availability_or_outage_does_not_become_operational_truth(self) -> None:
        for availability in ("AVAILABLE", "UNAVAILABLE"):
            with self.subTest(availability=availability):
                result = assess_service_resilience(
                    context(),
                    as_of=NOW,
                    capacity=capacity(),
                    dependencies=(
                        dependency(
                            availability=availability,
                            binding=evidence("INFERRED"),
                        ),
                    ),
                )
                assessment = result.dependency_assessments[0]
                self.assertEqual(assessment.conservative_state, "UNKNOWN")
                self.assertEqual(assessment.evidence_state, "UNVERIFIED")
                self.assertIn(
                    "CRITICAL_DEPENDENCY_NEEDS_EVIDENCE:dep:interface",
                    result.gap_codes,
                )

    def test_over_capacity_names_at_risk_commitments_and_communication_gap(self) -> None:
        result = assess_service_resilience(
            context(work_kind="PROFESSIONAL_SERVICE", role="Producer"),
            as_of=NOW,
            capacity=capacity(
                "OVER_CAPACITY",
                commitments=("job:a", "job:b", "job:c"),
                at_risk=("job:b", "job:c"),
            ),
        )
        self.assertEqual(result.disposition, "AT_RISK")
        self.assertEqual(
            result.affected_commitment_refs,
            ("job:b", "job:c"),
        )
        self.assertIn("OVER_CAPACITY", result.gap_codes)
        self.assertIn(
            "COMMUNICATION_OWNER_MISSING:job:b",
            result.gap_codes,
        )
        self.assertIn(
            "COMMUNICATION_OWNER_MISSING:job:c",
            result.gap_codes,
        )

    def test_stale_capacity_does_not_reuse_old_within_capacity_claim(self) -> None:
        stale_capacity = capacity(
            binding=evidence(
                "USER_DECLARED",
                observed_delta=timedelta(days=-3),
                valid_for=timedelta(hours=2),
            )
        )
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=stale_capacity,
        )
        self.assertEqual(result.capacity_state, "UNKNOWN")
        self.assertEqual(result.disposition, "NEEDS_EVIDENCE")
        self.assertIn("CAPACITY_REVALIDATION_REQUIRED", result.gap_codes)

    def test_active_disruption_without_recovery_plan_is_recovery_blocked(self) -> None:
        event = DisruptionEvent(
            id="incident:interface",
            kind="EQUIPMENT_FAILURE",
            state="ACTIVE",
            statement="Interface stopped passing audio.",
            affected_commitment_refs=("job:mix-1",),
            dependency_ids=("dep:interface",),
            source_kind="USER_DECLARED",
            source_ref="incident:note",
            observed_at=NOW - timedelta(minutes=10),
        )
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=capacity(),
            dependencies=(
                dependency(
                    availability="UNAVAILABLE",
                    binding=evidence("USER_DECLARED"),
                ),
            ),
            disruptions=(event,),
        )
        self.assertEqual(result.disposition, "RECOVERY_BLOCKED")
        self.assertEqual(result.active_disruption_ids, ("incident:interface",))
        self.assertIn("RECOVERY_PLAN_MISSING", result.gap_codes)

    def test_ready_recovery_and_owned_communication_stay_non_executing(self) -> None:
        fallback = FallbackOption(
            id="fallback:spare",
            label="Spare interface",
            availability="AVAILABLE",
            evidence=evidence("OBSERVED"),
        )
        item = dependency(
            availability="UNAVAILABLE",
            binding=evidence("USER_DECLARED"),
            fallbacks=(fallback,),
        )
        event = DisruptionEvent(
            id="incident:interface",
            kind="EQUIPMENT_FAILURE",
            state="ACTIVE",
            statement="Primary interface failed.",
            affected_commitment_refs=("job:mix-1",),
            dependency_ids=("dep:interface",),
            source_kind="USER_DECLARED",
            source_ref="incident:note",
            observed_at=NOW - timedelta(minutes=5),
        )
        step = RecoveryStep(
            id="step:switch",
            owner="Mix Engineer",
            action="Switch the session to the observed spare interface.",
            state="READY",
            applies_to_disruption_ids=("incident:interface",),
        )
        comm = CommunicationNeed(
            id="comm:client",
            commitment_ref="job:mix-1",
            audience="client",
            owner="Mix Engineer",
            channel="email",
            reason="Service disruption may affect delivery timing.",
        )
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=capacity(),
            dependencies=(item,),
            disruptions=(event,),
            recovery_steps=(step,),
            communication_needs=(comm,),
        )
        self.assertEqual(result.disposition, "DISRUPTED")
        self.assertEqual(result.recovery_blocker_step_ids, ())
        self.assertEqual(result.communication_need_ids, ("comm:client",))
        self.assertFalse(result.messaging_authorized)
        self.assertFalse(result.external_action_authorized)

    def test_unmet_recovery_prerequisite_is_explicit_blocker(self) -> None:
        event = DisruptionEvent(
            id="incident:venue",
            kind="VENUE_DISRUPTION",
            state="ACTIVE",
            statement="Venue access is unavailable.",
            affected_commitment_refs=("show:1",),
            dependency_ids=("dep:venue",),
            source_kind="VERIFIED_EXTERNAL",
            source_ref="venue:notice",
            observed_at=NOW - timedelta(minutes=30),
        )
        first = RecoveryStep(
            id="step:confirm-alt",
            owner="Booking Agent",
            action="Confirm alternate venue availability.",
            state="PENDING",
            applies_to_disruption_ids=("incident:venue",),
        )
        second = RecoveryStep(
            id="step:advance-alt",
            owner="Tour Manager",
            action="Advance the alternate venue.",
            state="READY",
            applies_to_disruption_ids=("incident:venue",),
            prerequisite_step_ids=("step:confirm-alt",),
        )
        venue = ResilienceDependency(
            id="dep:venue",
            kind="VENUE",
            label="Primary venue",
            critical=True,
            availability="UNAVAILABLE",
            commitment_refs=("show:1",),
            evidence=evidence("VERIFIED_EXTERNAL", source_ref="venue:notice"),
        )
        comm = CommunicationNeed(
            id="comm:promoter",
            commitment_ref="show:1",
            audience="promoter and team",
            owner="Booking Agent",
            channel="phone",
            reason="Venue disruption.",
        )
        result = assess_service_resilience(
            context("LIVE_SERVICE", role="Booking Agent"),
            as_of=NOW,
            capacity=capacity(
                commitments=("show:1",),
                binding=evidence("USER_DECLARED"),
            ),
            dependencies=(venue,),
            disruptions=(event,),
            recovery_steps=(first, second),
            communication_needs=(comm,),
        )
        self.assertEqual(result.disposition, "RECOVERY_BLOCKED")
        self.assertIn("step:advance-alt", result.recovery_blocker_step_ids)
        self.assertIn("RECOVERY_PREREQUISITES_BLOCKED", result.gap_codes)

    def test_live_transport_dependency_differs_from_studio_equipment(self) -> None:
        transport = ResilienceDependency(
            id="dep:van",
            kind="TRANSPORT",
            label="Tour van",
            critical=True,
            availability="UNAVAILABLE",
            commitment_refs=("show:2",),
            evidence=evidence("OBSERVED", source_ref="transport:inspection"),
        )
        comm = CommunicationNeed(
            id="comm:tour",
            commitment_ref="show:2",
            audience="tour team",
            owner="Tour Manager",
            channel="group message",
            reason="Transport unavailable.",
        )
        result = assess_service_resilience(
            context("TRAVEL", role="Tour Manager"),
            as_of=NOW,
            capacity=capacity(
                commitments=("show:2",),
                binding=evidence("USER_DECLARED"),
            ),
            dependencies=(transport,),
            communication_needs=(comm,),
        )
        self.assertEqual(result.disposition, "AT_RISK")
        self.assertEqual(result.affected_commitment_refs, ("show:2",))
        self.assertIn(
            "CRITICAL_DEPENDENCY_UNAVAILABLE:dep:van",
            result.gap_codes,
        )

    def test_insurance_record_presence_never_certifies_coverage(self) -> None:
        insurance_record = ResilienceDependency(
            id="dep:insurance-record",
            kind="INSURANCE_RECORD",
            label="Studio insurance record",
            critical=False,
            availability="AVAILABLE",
            commitment_refs=(),
            evidence=evidence(
                "VERIFIED_EXTERNAL",
                source_ref="document:policy-record",
            ),
        )
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=capacity(),
            dependencies=(insurance_record,),
        )
        self.assertFalse(result.insurance_coverage_certified)
        self.assertFalse(result.legal_sufficiency_certified)
        self.assertEqual(result.disposition, "STABLE")

    def test_future_or_unknown_disruption_evidence_fails_closed(self) -> None:
        future = DisruptionEvent(
            id="incident:future",
            kind="OTHER",
            state="ACTIVE",
            statement="Future-dated incident.",
            affected_commitment_refs=("job:mix-1",),
            dependency_ids=(),
            source_kind="USER_DECLARED",
            source_ref="note:future",
            observed_at=NOW + timedelta(minutes=1),
        )
        unknown = DisruptionEvent(
            id="incident:unknown",
            kind="OTHER",
            state="UNKNOWN",
            statement="Current disruption state is unknown.",
            affected_commitment_refs=("job:mix-1",),
            dependency_ids=(),
            source_kind="USER_DECLARED",
            source_ref="note:unknown",
            observed_at=NOW - timedelta(minutes=1),
        )
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=capacity(),
            disruptions=(future, unknown),
        )
        self.assertEqual(result.disposition, "NEEDS_EVIDENCE")
        self.assertIn(
            "DISRUPTION_EVIDENCE_FUTURE:incident:future",
            result.gap_codes,
        )
        self.assertIn(
            "DISRUPTION_STATE_UNKNOWN:incident:unknown",
            result.gap_codes,
        )

    def test_recovery_cycle_and_unknown_bindings_fail_closed(self) -> None:
        event = DisruptionEvent(
            id="incident:x",
            kind="OTHER",
            state="ACTIVE",
            statement="Disruption.",
            affected_commitment_refs=("job:mix-1",),
            dependency_ids=(),
            source_kind="USER_DECLARED",
            source_ref="note:x",
            observed_at=NOW,
        )
        a = RecoveryStep(
            id="step:a",
            owner="Owner",
            action="A",
            state="PENDING",
            applies_to_disruption_ids=("incident:x",),
            prerequisite_step_ids=("step:b",),
        )
        b = RecoveryStep(
            id="step:b",
            owner="Owner",
            action="B",
            state="PENDING",
            applies_to_disruption_ids=("incident:x",),
            prerequisite_step_ids=("step:a",),
        )
        with self.assertRaises(ValidationError):
            assess_service_resilience(
                context(),
                as_of=NOW,
                capacity=capacity(),
                disruptions=(event,),
                recovery_steps=(a, b),
            )
        bad = RecoveryStep(
            id="step:bad",
            owner="Owner",
            action="Bad",
            state="PENDING",
            applies_to_disruption_ids=("incident:missing",),
        )
        with self.assertRaises(ValidationError):
            assess_service_resilience(
                context(),
                as_of=NOW,
                capacity=capacity(),
                disruptions=(event,),
                recovery_steps=(bad,),
            )

    def test_duplicate_ids_and_scalar_collection_coercion_fail_closed(self) -> None:
        one = dependency()
        two = dependency()
        with self.assertRaises(ValidationError):
            assess_service_resilience(
                context(),
                as_of=NOW,
                capacity=capacity(),
                dependencies=(one, two),
            )
        with self.assertRaises(ValidationError):
            assess_service_resilience(
                context(),
                as_of=NOW,
                capacity=capacity(),
                dependencies=[one],  # type: ignore[arg-type]
            )
        with self.assertRaises(ValidationError):
            CapacitySnapshot(
                state="WITHIN_CAPACITY",
                commitment_refs="job:mix-1",  # type: ignore[arg-type]
                at_risk_commitment_refs=(),
                evidence=evidence(),
            )

    def test_digital_backup_is_not_smuggled_into_physical_service_owner(self) -> None:
        with self.assertRaises(ValidationError):
            ResilienceDependency(
                id="dep:backup",
                kind="DIGITAL_BACKUP",
                label="Backup",
                critical=True,
                availability="AVAILABLE",
                commitment_refs=("job:mix-1",),
                evidence=evidence(),
            )

    def test_authority_fields_are_hard_false_and_non_forgeable(self) -> None:
        result = assess_service_resilience(
            context(),
            as_of=NOW,
            capacity=capacity(),
        )
        self.assertFalse(result.grants_any_authority)
        for field_name in (
            "mutation_authorized",
            "messaging_authorized",
            "scheduling_authorized",
            "cancellation_authorized",
            "refund_authorized",
            "purchase_authorized",
            "spend_authorized",
            "contract_authorized",
            "insurance_coverage_certified",
            "legal_sufficiency_certified",
            "provider_action_authorized",
            "daw_action_authorized",
            "external_action_authorized",
        ):
            self.assertFalse(getattr(result, field_name))

        with self.assertRaises(TypeError):
            ServiceResilienceAssessment(
                disposition="STABLE",
                context=context(),
                capacity_state="WITHIN_CAPACITY",
                dependency_assessments=(),
                active_disruption_ids=(),
                affected_commitment_refs=(),
                communication_need_ids=(),
                recovery_blocker_step_ids=(),
                gap_codes=(),
                messaging_authorized=True,
            )

    def test_strict_boolean_and_timezone_boundaries(self) -> None:
        with self.assertRaises(ValidationError):
            ResilienceDependency(
                id="dep:x",
                kind="EQUIPMENT",
                label="X",
                critical=1,  # type: ignore[arg-type]
                availability="AVAILABLE",
                commitment_refs=(),
                evidence=evidence(),
            )
        with self.assertRaises(ValidationError):
            EvidenceBinding(
                source_kind="OBSERVED",
                source_ref="src",
                observed_at=datetime(2026, 9, 5, 1, 0),
                revalidate_after=datetime(2026, 9, 6, 1, 0),
            )
        with self.assertRaises(ValidationError):
            assess_service_resilience(
                context(),
                as_of=datetime(2026, 9, 5, 1, 0),
                capacity=capacity(),
            )


if __name__ == "__main__":
    unittest.main()
