from __future__ import annotations

import pytest

from n0te2.creative_partner_presence import (
    PRESENCE_LEVELS,
    CreativePartnerPresenceService,
    PresenceContextBinding,
    PresencePolicy,
    PresenceSignal,
    StalePresenceContextError,
    UnsupportedPresencePolicyError,
)
from n0te2.lineage import ValidationError


def _binding(**overrides) -> PresenceContextBinding:
    values = {
        "artist_id": "artist_one",
        "song_id": "song_one",
        "session_id": "session_one",
        "focus_id": "focus_one",
        "job_id": "job_one",
    }
    values.update(overrides)
    return PresenceContextBinding(**values)


def _signal(**overrides) -> PresenceSignal:
    values = {
        "semantic_key": "suggestion:arrangement:bridge",
        "purpose_relevant": True,
        "job_relevant": True,
        "changes_next_decision": True,
        "actionable_now": True,
    }
    values.update(overrides)
    return PresenceSignal(**values)


@pytest.mark.parametrize("level", PRESENCE_LEVELS)
def test_presence_levels_are_exact_ephemeral_and_never_grant_authority(level: str) -> None:
    binding = _binding()
    policy = PresencePolicy.explicit_session(binding, level)
    decision = CreativePartnerPresenceService().decide(
        policy, _signal(), current_binding=binding
    )

    assert policy.level == level
    assert policy.lifecycle == "EPHEMERAL"
    assert policy.source == "EXPLICIT_SESSION"
    assert decision.presence == level
    assert decision.authority_effect == "UNCHANGED"
    assert decision.action_authority_granted is False
    assert decision.mutation_authorized is False
    assert decision.external_action_authorized is False


def test_missing_policy_has_deterministic_quiet_safe_default() -> None:
    binding = _binding()
    policy = PresencePolicy.safe_default(binding)
    decision = CreativePartnerPresenceService().decide(
        policy, _signal(), current_binding=binding
    )

    assert policy.level == "QUIET"
    assert policy.source == "SAFE_DEFAULT"
    assert policy.lifecycle == "EPHEMERAL"
    assert decision.outcome == "NO_ACTION"
    assert decision.leave_it_alone is True
    assert decision.should_interrupt is False


@pytest.mark.parametrize("level", PRESENCE_LEVELS)
@pytest.mark.parametrize(
    "alert_kind",
    ("SAFETY", "CONTRADICTION", "STALE_CONTEXT", "RIGHTS_PRIVACY"),
)
def test_required_alerts_survive_every_presence_level_and_not_now(
    level: str, alert_kind: str
) -> None:
    binding = _binding()
    decision = CreativePartnerPresenceService().decide(
        PresencePolicy.explicit_session(binding, level),
        _signal(
            deferred_not_now=True,
            purpose_relevant=False,
            job_relevant=False,
            changes_next_decision=False,
            actionable_now=False,
            required_alert_kind=alert_kind,
        ),
        current_binding=binding,
    )

    assert decision.outcome == "REQUIRED_ALERT"
    assert decision.should_interrupt is True
    assert decision.initiative == "REQUIRED"
    assert decision.reason_codes == ("REQUIRED_ALERT", alert_kind)
    assert decision.action_authority_granted is False


@pytest.mark.parametrize("level", PRESENCE_LEVELS)
def test_explicit_artist_request_is_answered_even_in_quiet_or_after_deferral(
    level: str,
) -> None:
    binding = _binding()
    decision = CreativePartnerPresenceService().decide(
        PresencePolicy.explicit_session(binding, level),
        _signal(
            explicitly_requested=True,
            deferred_not_now=True,
            purpose_relevant=False,
            job_relevant=False,
            changes_next_decision=False,
            actionable_now=False,
        ),
        current_binding=binding,
    )

    assert decision.outcome == "RESPOND"
    assert decision.initiative == "REACTIVE"
    assert decision.reason_codes == ("EXPLICIT_REQUEST",)
    assert decision.mutation_authorized is False


@pytest.mark.parametrize("level", PRESENCE_LEVELS)
def test_not_now_suppresses_unsolicited_intervention_at_every_level(level: str) -> None:
    binding = _binding()
    decision = CreativePartnerPresenceService().decide(
        PresencePolicy.explicit_session(binding, level),
        _signal(deferred_not_now=True),
        current_binding=binding,
    )

    assert decision.outcome == "NO_ACTION"
    assert decision.should_interrupt is False
    assert decision.reason_codes == ("DEFERRED_NOT_NOW",)


@pytest.mark.parametrize("level", PRESENCE_LEVELS)
def test_musical_relatedness_without_material_relevance_stays_silent(level: str) -> None:
    binding = _binding()
    decision = CreativePartnerPresenceService().decide(
        PresencePolicy.explicit_session(binding, level),
        _signal(
            changes_next_decision=False,
            protects_context=False,
            prevents_meaningful_failure=False,
        ),
        current_binding=binding,
    )

    assert decision.outcome == "NO_ACTION"
    assert decision.reason_codes == ("NO_MATERIAL_RELEVANCE",)


def test_four_presence_levels_have_materially_distinct_initiative() -> None:
    binding = _binding()
    service = CreativePartnerPresenceService()

    decisions = {
        level: service.decide(
            PresencePolicy.explicit_session(binding, level),
            _signal(),
            current_binding=binding,
        )
        for level in PRESENCE_LEVELS
    }

    assert decisions["QUIET"].outcome == "NO_ACTION"
    assert decisions["QUIET"].initiative == "NONE"
    assert decisions["NUDGE"].outcome == "NUDGE"
    assert decisions["NUDGE"].initiative == "LIGHT"
    assert decisions["COLLABORATE"].outcome == "COLLABORATE"
    assert decisions["COLLABORATE"].initiative == "PARTNER"
    assert decisions["LEAD"].outcome == "LEAD"
    assert decisions["LEAD"].initiative == "STRUCTURE_NEXT_DECISION"
    assert all(
        decision.action_authority_granted is False
        and decision.mutation_authorized is False
        and decision.external_action_authorized is False
        for decision in decisions.values()
    )


def test_nudge_rejects_low_impact_or_high_cost_non_failure_interruption() -> None:
    binding = _binding()
    policy = PresencePolicy.explicit_session(binding, "NUDGE")
    service = CreativePartnerPresenceService()

    low_impact = service.decide(
        policy,
        _signal(
            changes_next_decision=False,
            protects_context=True,
            prevents_meaningful_failure=False,
        ),
        current_binding=binding,
    )
    high_cost = service.decide(
        policy,
        _signal(interruption_cost="HIGH"),
        current_binding=binding,
    )
    failure_prevention = service.decide(
        policy,
        _signal(
            changes_next_decision=False,
            prevents_meaningful_failure=True,
            interruption_cost="HIGH",
        ),
        current_binding=binding,
    )

    assert low_impact.outcome == "NO_ACTION"
    assert low_impact.reason_codes == ("BELOW_NUDGE_THRESHOLD",)
    assert high_cost.outcome == "NO_ACTION"
    assert high_cost.reason_codes == ("HIGH_INTERRUPTION_COST",)
    assert failure_prevention.outcome == "NUDGE"


def test_collaborate_respects_interruption_cost_but_lead_can_structure_relevant_work() -> None:
    binding = _binding()
    service = CreativePartnerPresenceService()
    signal = _signal(
        changes_next_decision=False,
        protects_context=True,
        interruption_cost="HIGH",
    )

    collaborate = service.decide(
        PresencePolicy.explicit_session(binding, "COLLABORATE"),
        signal,
        current_binding=binding,
    )
    lead = service.decide(
        PresencePolicy.explicit_session(binding, "LEAD"),
        signal,
        current_binding=binding,
    )

    assert collaborate.outcome == "NO_ACTION"
    assert collaborate.reason_codes == ("HIGH_INTERRUPTION_COST",)
    assert lead.outcome == "LEAD"
    assert lead.initiative == "STRUCTURE_NEXT_DECISION"
    assert lead.action_authority_granted is False


@pytest.mark.parametrize("level", ("NUDGE", "COLLABORATE", "LEAD"))
def test_non_actionable_unsolicited_work_stays_silent(level: str) -> None:
    binding = _binding()
    decision = CreativePartnerPresenceService().decide(
        PresencePolicy.explicit_session(binding, level),
        _signal(actionable_now=False),
        current_binding=binding,
    )

    assert decision.outcome == "NO_ACTION"
    assert decision.reason_codes == ("NOT_ACTIONABLE_NOW",)


def test_unrelated_work_must_protect_context_or_prevent_failure_to_compete_for_attention() -> None:
    binding = _binding()
    service = CreativePartnerPresenceService()
    policy = PresencePolicy.explicit_session(binding, "LEAD")

    unrelated = service.decide(
        policy,
        _signal(
            purpose_relevant=False,
            job_relevant=False,
            changes_next_decision=True,
            protects_context=False,
            prevents_meaningful_failure=False,
        ),
        current_binding=binding,
    )
    protective = service.decide(
        policy,
        _signal(
            purpose_relevant=False,
            job_relevant=False,
            changes_next_decision=False,
            protects_context=True,
        ),
        current_binding=binding,
    )

    assert unrelated.outcome == "NO_ACTION"
    assert unrelated.reason_codes == ("OUTSIDE_CURRENT_PURPOSE_AND_JOB",)
    assert protective.outcome == "LEAD"


def test_presence_binding_fails_closed_when_song_session_focus_or_job_changes() -> None:
    original = _binding()
    policy = PresencePolicy.explicit_session(original, "LEAD")
    service = CreativePartnerPresenceService()

    for changed in (
        _binding(song_id="song_two"),
        _binding(session_id="session_two"),
        _binding(focus_id="focus_two"),
        _binding(job_id="job_two"),
    ):
        with pytest.raises(StalePresenceContextError):
            service.decide(policy, _signal(), current_binding=changed)


def test_binding_fingerprint_is_deterministic_and_changes_with_context() -> None:
    first = _binding()
    same = _binding()
    changed = _binding(session_id="session_two")

    assert first.fingerprint == same.fingerprint
    assert first.fingerprint != changed.fingerprint


def test_unknown_future_policy_version_fails_safely() -> None:
    with pytest.raises(UnsupportedPresencePolicyError):
        PresencePolicy(binding=_binding(), schema_version=2)


def test_persistence_cannot_be_smuggled_into_ephemeral_presence_policy() -> None:
    with pytest.raises(ValidationError):
        PresencePolicy(binding=_binding(), lifecycle="DURABLE")


@pytest.mark.parametrize(
    "invalid_level",
    ("DO_IT", "WITH_ME", "SHOW_ME", "EXPLAIN_WHY", "LET_ME_TRY", "TRY", "DO"),
)
def test_collaboration_depth_or_authority_modes_cannot_be_used_as_presence(
    invalid_level: str,
) -> None:
    with pytest.raises(ValidationError):
        PresencePolicy.explicit_session(_binding(), invalid_level)


def test_relevance_inputs_are_qualitative_not_numeric_scores() -> None:
    signal = _signal()
    assert not hasattr(signal, "score")
    assert not hasattr(signal, "confidence")
    assert isinstance(signal.changes_next_decision, bool)
    assert isinstance(signal.protects_context, bool)
    assert isinstance(signal.prevents_meaningful_failure, bool)


def test_invalid_signal_semantics_fail_before_any_decision() -> None:
    with pytest.raises(ValidationError):
        PresenceSignal(
            semantic_key="",
            purpose_relevant=True,
            job_relevant=True,
        )
    with pytest.raises(ValidationError):
        _signal(interruption_cost="EXTREME")
    with pytest.raises(ValidationError):
        _signal(required_alert_kind="PROMOTION")


def test_presence_decisions_do_not_mutate_the_policy_or_binding() -> None:
    binding = _binding()
    policy = PresencePolicy.explicit_session(binding, "LEAD")
    before = (policy, binding, policy.binding.fingerprint)

    for signal in (
        _signal(),
        _signal(deferred_not_now=True),
        _signal(required_alert_kind="STALE_CONTEXT"),
        _signal(explicitly_requested=True),
    ):
        CreativePartnerPresenceService().decide(
            policy, signal, current_binding=binding
        )

    assert before == (policy, binding, policy.binding.fingerprint)
