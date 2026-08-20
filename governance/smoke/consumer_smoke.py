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

if active != "CORE-03" or increment != "CORE-03D":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import (  # noqa: E402
    CapabilityCandidate,
    RecipeDefinition,
    RecipePlanner,
    RecipeStep,
    ResolutionConstraints,
    StudioCapabilityProfile,
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


recipe = RecipeDefinition(
    recipe_id="recipe:vocal:tighten-chorus",
    name="Tighten Chorus Vocals",
    intent="Inspect, prepare and compare a vocal-tightening candidate without granting execution authority",
    steps=(
        RecipeStep(
            step_id="inspect",
            capability="vocal.timing.inspect",
            description="Inspect chorus vocal timing relationships",
            authority_class="READ_ONLY",
            postconditions=("timing relationships are inspectable",),
            recovery_policy="NONE",
        ),
        RecipeStep(
            step_id="tighten",
            capability="vocal.tighten",
            description="Prepare a reversible timing correction candidate",
            authority_class="REVERSIBLE_MUTATION",
            postconditions=("candidate timing is inspectable", "original remains recoverable"),
            recovery_policy="ROLLBACK_REQUIRED",
            depends_on=("inspect",),
        ),
        RecipeStep(
            step_id="compare",
            capability="audio.compare",
            description="Compare original and tightened candidates",
            authority_class="READ_ONLY",
            postconditions=("comparison result is inspectable",),
            recovery_policy="RETRY_SAFE",
            depends_on=("tighten",),
        ),
    ),
)
planner = RecipePlanner()

# Recipe structure carries semantic orchestration only, never host/provider/candidate/approval state.
assert set(RecipeDefinition.__dataclass_fields__) == {"recipe_id", "name", "intent", "steps"}
assert set(RecipeStep.__dataclass_fields__) == {
    "step_id",
    "capability",
    "description",
    "authority_class",
    "postconditions",
    "recovery_policy",
    "depends_on",
}

# Studio A can resolve the entire Recipe with a host-native mutation route.
studio_a_facts = [
    candidate("a-inspect", "HOST_NATIVE", "vocal.timing.inspect", task_fit=0.95),
    candidate("a-tighten", "HOST_NATIVE", "vocal.tighten", task_fit=0.97),
    candidate("a-compare", "GUIDED", "audio.compare", locality=1.0, privacy=1.0),
]
studio_a = StudioCapabilityProfile.build(
    environment_id="studio:a",
    host_label="Ableton Live",
    candidates=studio_a_facts,
)
ready_a = planner.plan(recipe, studio_a)
assert ready_a.status == "READY"
assert ready_a.unavailable_step_ids == ()
assert tuple(item.step.step_id for item in ready_a.step_plans) == (
    "inspect", "tighten", "compare"
)
tighten_a = next(item for item in ready_a.step_plans if item.step.step_id == "tighten")
assert tighten_a.resolution.recommended.candidate.route_kind == "HOST_NATIVE"

# The same Recipe in Studio B uses different legitimate routes but keeps the same semantic identity.
studio_b = StudioCapabilityProfile.build(
    environment_id="studio:b",
    host_label="Logic Pro",
    candidates=[
        candidate("b-inspect", "N0TE_NATIVE", "vocal.timing.inspect", task_fit=0.94),
        candidate("b-tighten", "OWNED_TOOL", "vocal.tighten", task_fit=0.93),
        candidate("b-compare", "N0TE_NATIVE", "audio.compare"),
    ],
)
ready_b = planner.plan(recipe, studio_b)
assert ready_b.status == "READY"
assert ready_b.recipe_id == ready_a.recipe_id == recipe.recipe_id
tighten_b = next(item for item in ready_b.step_plans if item.step.step_id == "tighten")
assert tighten_b.resolution.recommended.candidate.route_kind == "OWNED_TOOL"

# A famous installed but unverified tool remains unavailable and makes the Recipe unavailable.
studio_c = StudioCapabilityProfile.build(
    environment_id="studio:c",
    host_label="Pro Tools",
    candidates=[
        candidate("c-inspect", "GUIDED", "vocal.timing.inspect"),
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
        ),
        candidate("c-compare", "GUIDED", "audio.compare"),
    ],
)
unavailable = planner.plan(recipe, studio_c)
assert unavailable.status == "UNAVAILABLE"
assert unavailable.unavailable_step_ids == ("tighten",)
tighten_c = next(item for item in unavailable.step_plans if item.step.step_id == "tighten")
assert "UNVERIFIED" in tighten_c.resolution.reason_codes

# Host label changes alone cannot alter the plan.
studio_a_other_label = StudioCapabilityProfile.build(
    environment_id="studio:a",
    host_label="Generic Other",
    candidates=studio_a_facts,
)
assert planner.plan(recipe, studio_a_other_label) == ready_a

# Consequential authority is a future requirement declaration only, even if resolution is READY.
consequential = RecipeDefinition(
    recipe_id="recipe:content:publish-preview",
    name="Preview Publish Route",
    intent="Plan an outbound content route without authorizing or executing publication",
    steps=(
        RecipeStep(
            step_id="publish",
            capability="content.publish.prepare",
            description="Prepare the exact outbound publishing route for later approval",
            authority_class="CONSEQUENTIAL_ACTION",
            postconditions=("destination and payload would be inspectable before execution",),
            recovery_policy="MANUAL_RECOVERY",
        ),
    ),
)
consequential_studio = StudioCapabilityProfile.build(
    environment_id="studio:consequential",
    candidates=[candidate("guided-publish", "GUIDED", "content.publish.prepare")],
)
consequential_plan = planner.plan(consequential, consequential_studio)
assert consequential_plan.status == "READY"
assert consequential_plan.declared_authority_classes == ("CONSEQUENTIAL_ACTION",)
for forbidden in ("approved", "authorization", "executed", "receipt"):
    assert not hasattr(consequential_plan, forbidden)

# Resolver constraints still govern a consequential route before any future authority layer.
cloud_studio = StudioCapabilityProfile.build(
    environment_id="studio:cloud",
    candidates=[
        candidate(
            "cloud-publish",
            "PROVIDER",
            "content.publish.prepare",
            locality=0.1,
            privacy=0.2,
            paid=True,
        )
    ],
)
constrained = planner.plan(
    consequential,
    cloud_studio,
    ResolutionConstraints(min_locality=0.9, min_privacy=0.9, allow_paid=False),
)
assert constrained.status == "UNAVAILABLE"
reasons = set(constrained.step_plans[0].resolution.reason_codes)
assert {"LOCALITY_BELOW_MINIMUM", "PRIVACY_BELOW_MINIMUM", "PAID_ROUTE_NOT_ALLOWED"} <= reasons

# Planning is pure and deterministic; nothing executed or mutated.
assert planner.plan(recipe, studio_a) == ready_a
assert planner.plan(recipe, studio_b) == ready_b
assert planner.plan(recipe, studio_c) == unavailable

print(
    "CORE-03D CONSUMER SMOKE: GREEN: one ordered host-neutral Recipe preserved capability, postcondition, recovery and future-authority declarations across studios; routes changed truthfully, unverified tools stayed unusable, and planning granted no approval or execution authority"
)
