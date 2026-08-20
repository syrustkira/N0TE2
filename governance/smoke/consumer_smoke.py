#!/usr/bin/env python3
"""Stage-aware construction smoke for the active bounded consumer outcome."""
from __future__ import annotations

import json
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))

state = json.loads((repo / "governance/current_state.json").read_text())
active = state["active_node"]
increment = state.get("active_increment")

if active in {"BOOT-02", "LEGACY-01"}:
    for forbidden in ("app", "src", "n0te2", "legacy"):
        path = repo / forbidden
        if path.exists() and any(path.rglob("*")):
            print(
                f"PRE-PRODUCT SMOKE: RED: product implementation appeared early: {forbidden}/",
                file=sys.stderr,
            )
            raise SystemExit(1)
    print("PRE-PRODUCT SMOKE: GREEN")
    raise SystemExit(0)

if active != "CORE-03" or increment != "CORE-03C":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import (  # noqa: E402
    CapabilityCandidate,
    StudioCapabilityProfile,
    TemplateDefinition,
    TemplatePlanner,
    TemplateRole,
)


def candidate(candidate_id: str, route_kind: str, capability: str, **overrides):
    values = dict(
        candidate_id=candidate_id,
        route_kind=route_kind,
        capability=capability,
        display_name=candidate_id,
        brand=None,
        verified=True,
        compatible=True,
        evidence_ref=f"smoke:{candidate_id}",
        evidence_age_seconds=10,
        task_fit=0.80,
        editability=0.80,
        locality=0.80,
        privacy=0.80,
        latency=0.80,
        reversibility=1.0,
        cost_efficiency=0.80,
        portability=0.80,
        user_preference=0.50,
        paid=False,
    )
    values.update(overrides)
    return CapabilityCandidate(**values)


template = TemplateDefinition(
    template_id="template:vocal:production-start",
    family="VOCAL",
    name="Vocal Production Start",
    intent="Start a vocal production pass without baking any DAW into the reusable structure",
    roles=(
        TemplateRole(
            role_id="lead-edit",
            capability="vocal.tighten",
            description="Tighten the lead vocal while preserving performance intent",
            required=True,
            tags=("editing", "lead"),
        ),
        TemplateRole(
            role_id="harmony-build",
            capability="vocal.harmony.build",
            description="Build supporting harmonies when the current studio can do so legitimately",
            required=False,
            tags=("harmony", "support"),
        ),
    ),
)
planner = TemplatePlanner()

# Template structure itself contains no provider/host/candidate/track identity fields.
assert set(TemplateDefinition.__dataclass_fields__) == {
    "template_id", "family", "name", "intent", "roles"
}
assert set(TemplateRole.__dataclass_fields__) == {
    "role_id", "capability", "description", "required", "tags"
}

# Studio A can realize every role, so the exact Template is FULL.
studio_a_facts = [
    candidate(
        "a-lead-native",
        "HOST_NATIVE",
        "vocal.tighten",
        task_fit=0.97,
        locality=1.0,
        privacy=1.0,
    ),
    candidate(
        "a-harmony-guided",
        "GUIDED",
        "vocal.harmony.build",
        task_fit=0.75,
        locality=1.0,
        privacy=1.0,
    ),
]
studio_a = StudioCapabilityProfile.build(
    environment_id="studio:a",
    host_label="Ableton Live",
    candidates=reversed(studio_a_facts),
)
full = planner.plan(template, studio_a)
assert full.status == "FULL"
assert full.template_id == template.template_id
assert full.unavailable_required_role_ids == ()
assert full.unavailable_optional_role_ids == ()
lead_a = next(item for item in full.role_plans if item.role.role_id == "lead-edit")
assert lead_a.resolution.recommended.candidate.route_kind == "HOST_NATIVE"

# Changing only the descriptive host label cannot change the plan.
studio_a_other_label = StudioCapabilityProfile.build(
    environment_id="studio:a",
    host_label="Generic Other",
    candidates=studio_a_facts,
)
assert planner.plan(template, studio_a_other_label) == full

# Studio B can do the required role but lacks the optional harmony role: PARTIAL.
studio_b = StudioCapabilityProfile.build(
    environment_id="studio:b",
    host_label="Logic Pro",
    candidates=[
        candidate(
            "b-lead-n0te",
            "N0TE_NATIVE",
            "vocal.tighten",
            task_fit=0.94,
            locality=1.0,
            privacy=1.0,
        )
    ],
)
partial = planner.plan(template, studio_b)
assert partial.status == "PARTIAL"
assert partial.unavailable_required_role_ids == ()
assert partial.unavailable_optional_role_ids == ("harmony-build",)
lead_b = next(item for item in partial.role_plans if item.role.role_id == "lead-edit")
assert lead_b.resolution.recommended.candidate.route_kind == "N0TE_NATIVE"

# Studio C advertises a famous installed tool, but it is unverified and cannot satisfy the required role.
studio_c = StudioCapabilityProfile.build(
    environment_id="studio:c",
    host_label="Pro Tools",
    candidates=[
        candidate(
            "c-famous-installed",
            "OWNED_TOOL",
            "vocal.tighten",
            display_name="Famous Vocal Tool",
            brand="Famous Vendor",
            verified=False,
            evidence_ref=None,
            task_fit=1.0,
            user_preference=1.0,
        )
    ],
)
unavailable = planner.plan(template, studio_c)
assert unavailable.status == "UNAVAILABLE"
assert unavailable.unavailable_required_role_ids == ("lead-edit",)
lead_c = next(item for item in unavailable.role_plans if item.role.role_id == "lead-edit")
assert "UNVERIFIED" in lead_c.resolution.reason_codes

# One Template identity survives every environment; only the support plan changes.
assert template == TemplateDefinition(
    template_id="template:vocal:production-start",
    family="vocal",
    name="Vocal Production Start",
    intent="Start a vocal production pass without baking any DAW into the reusable structure",
    roles=tuple(reversed(template.roles)),
)
assert planner.plan(template, studio_a) == full
assert planner.plan(template, studio_b) == partial
assert planner.plan(template, studio_c) == unavailable

print(
    "CORE-03C CONSUMER SMOKE: GREEN: one provider-neutral Template kept the same semantic identity while truthful Studio facts produced FULL, PARTIAL and UNAVAILABLE plans; host labels and famous unverified tools gained no authority and no host was mutated"
)
