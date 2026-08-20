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

if active != "CORE-03" or increment != "CORE-03B":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import (  # noqa: E402
    CapabilityCandidate,
    N0TEableJob,
    ResolutionConstraints,
    StudioCapabilityProfile,
)

job = N0TEableJob(
    id="job:vocal.tighten",
    capability="vocal.tighten",
    description="Tighten chorus vocals while preserving performance intent",
)


def candidate(candidate_id: str, route_kind: str, **overrides):
    values = dict(
        candidate_id=candidate_id,
        route_kind=route_kind,
        capability=job.capability,
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


# Studio A truth makes the host-native route strongest. The host label itself is not evidence.
studio_a_facts = [
    candidate(
        "a-native",
        "HOST_NATIVE",
        display_name="Native Vocal Timing",
        brand="Host Vendor",
        task_fit=0.98,
        editability=0.95,
        locality=1.0,
        privacy=1.0,
        latency=0.98,
    ),
    candidate(
        "a-guided",
        "GUIDED",
        task_fit=0.62,
        locality=1.0,
        privacy=1.0,
    ),
]
studio_a = StudioCapabilityProfile.build(
    environment_id="studio:a",
    host_label="Ableton Live",
    candidates=reversed(studio_a_facts),
)
studio_a_same_facts_other_label = StudioCapabilityProfile.build(
    environment_id="studio:a",
    host_label="Generic Other",
    candidates=studio_a_facts,
)
resolution_a = studio_a.resolve(job)
assert resolution_a.status == "RESOLVED"
assert resolution_a.recommended.candidate.route_kind == "HOST_NATIVE"
assert resolution_a == studio_a_same_facts_other_label.resolve(job)
assert tuple(item.candidate_id for item in studio_a.candidates) == (
    "a-guided",
    "a-native",
)

# Studio B has a host-native fact, but it is incompatible. A verified N0TE-native route wins instead.
studio_b = StudioCapabilityProfile.build(
    environment_id="studio:b",
    host_label="Logic Pro",
    candidates=[
        candidate(
            "b-native",
            "HOST_NATIVE",
            compatible=False,
            task_fit=1.0,
        ),
        candidate(
            "b-n0te",
            "N0TE_NATIVE",
            task_fit=0.94,
            editability=0.92,
            locality=1.0,
            privacy=1.0,
        ),
        candidate(
            "b-installed-famous-plugin",
            "OWNED_TOOL",
            display_name="Famous Tightener",
            brand="Famous Vendor",
            verified=False,
            evidence_ref=None,
            task_fit=1.0,
            user_preference=1.0,
        ),
    ],
)
resolution_b = studio_b.resolve(job)
assert resolution_b.status == "RESOLVED"
assert resolution_b.job_id == resolution_a.job_id == job.id
assert resolution_b.recommended.candidate.route_kind == "N0TE_NATIVE"
rejected_b = {item.candidate_id: item.reason_codes for item in resolution_b.rejected}
assert "INCOMPATIBLE" in rejected_b["b-native"]
assert "UNVERIFIED" in rejected_b["b-installed-famous-plugin"]
summary_b = {item.route_kind: item for item in studio_b.route_summary()}
assert summary_b["OWNED_TOOL"].verified_candidate_ids == ()
assert summary_b["OWNED_TOOL"].unverified_candidate_ids == (
    "b-installed-famous-plugin",
)

# Studio C truthfully exposes a gap when its only represented route violates the artist constraints.
studio_c = StudioCapabilityProfile.build(
    environment_id="studio:c",
    host_label="Pro Tools",
    candidates=[
        candidate(
            "c-cloud-provider",
            "PROVIDER",
            brand="Cloud Vendor",
            locality=0.10,
            privacy=0.20,
            paid=True,
            user_preference=1.0,
        )
    ],
)
constraints = ResolutionConstraints(
    min_locality=0.90,
    min_privacy=0.90,
    allow_paid=False,
    require_reversible=True,
    max_evidence_age_seconds=30,
)
resolution_c = studio_c.resolve(job, constraints)
assert resolution_c.status == "UNAVAILABLE"
assert resolution_c.recommended is None
gaps = studio_c.gaps([job], constraints)
assert len(gaps) == 1
assert gaps[0].job_id == job.id
assert gaps[0].rejected_candidate_ids == ("c-cloud-provider",)
for reason in (
    "LOCALITY_BELOW_MINIMUM",
    "PRIVACY_BELOW_MINIMUM",
    "PAID_ROUTE_NOT_ALLOWED",
):
    assert reason in gaps[0].reason_codes

# Equal facts remain equal regardless of route/brand. Stable candidate ID is the explicit tie-break.
tie_profile = StudioCapabilityProfile.build(
    environment_id="studio:tie",
    host_label="Studio One",
    candidates=[
        candidate("tie-z", "HOST_NATIVE", brand="Native Brand"),
        candidate("tie-a", "PROVIDER", brand="Provider Brand"),
    ],
)
tie = tie_profile.resolve(job)
assert tie.candidate_ids == ("tie-a", "tie-z")
assert tie.recommended.score == tie.fallbacks[0].score
assert "SCORE_TIE_BROKEN_BY_CANDIDATE_ID" in tie.reason_codes

# Pure read model: repeated profile summaries and resolutions are identical and no persistence exists.
assert studio_b.route_summary() == studio_b.route_summary()
assert studio_b.resolve(job) == resolution_b
assert studio_c.gaps([job], constraints) == gaps

print(
    "CORE-03B CONSUMER SMOKE: GREEN: the same N0TEable job resolved from truthful per-studio capability facts; host labels/brands created no priority, an unverified installed tool remained unusable, and constrained missing capability surfaced as an explicit gap"
)
