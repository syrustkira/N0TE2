from __future__ import annotations

import pytest

from n0te2.creative_partner_presence import (
    PresenceContextBinding,
    PresenceDecision,
    PresencePolicy,
    PresenceSignal,
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
    }
    values.update(overrides)
    return PresenceSignal(**values)


def test_context_identifiers_are_not_coerced_from_non_text_values() -> None:
    with pytest.raises(ValidationError):
        PresenceContextBinding(artist_id=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _binding(song_id=42)


def test_policy_version_and_labels_fail_closed_instead_of_using_python_coercion() -> None:
    with pytest.raises(UnsupportedPresencePolicyError):
        PresencePolicy(binding=_binding(), schema_version=True)
    with pytest.raises(ValidationError):
        PresencePolicy(binding=_binding(), source=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        PresencePolicy.explicit_session(_binding(), None)  # type: ignore[arg-type]


def test_relevance_boolean_and_alert_inputs_require_exact_semantic_types() -> None:
    with pytest.raises(ValidationError):
        _signal(purpose_relevant=1)
    with pytest.raises(ValidationError):
        _signal(actionable_now="yes")
    with pytest.raises(ValidationError):
        _signal(required_alert_kind=1)


def test_presence_decision_constructor_cannot_forge_action_authority() -> None:
    common = {
        "presence": "LEAD",
        "outcome": "LEAD",
        "should_interrupt": True,
        "initiative": "STRUCTURE_NEXT_DECISION",
        "reason_codes": ("MATERIAL_LEAD",),
        "binding_fingerprint": "0" * 64,
        "policy_version": 1,
    }
    with pytest.raises(TypeError):
        PresenceDecision(**common, action_authority_granted=True)
    with pytest.raises(TypeError):
        PresenceDecision(**common, mutation_authorized=True)
    with pytest.raises(TypeError):
        PresenceDecision(**common, external_action_authorized=True)
    with pytest.raises(TypeError):
        PresenceDecision(**common, authority_effect="GRANTED")
