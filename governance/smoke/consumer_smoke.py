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

if active != "CORE-03" or increment != "CORE-03A":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import (  # noqa: E402
    CapabilityCandidate,
    CapabilityResolver,
    N0TEableJob,
    ResolutionConstraints,
)

job = N0TEableJob(
    id="job:vocal.tighten",
    capability="vocal.tighten",
    description="Tighten chorus vocals while preserving performance intent",
)
resolver = CapabilityResolver()


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


studio_a = resolver.resolve(
    job,
    [
        candidate(
            "studio-a-native",
            "HOST_NATIVE",
            task_fit=0.99,
            editability=0.95,
            locality=1.0,
            privacy=1.0,
            latency=0.98,
        ),
        candidate(
            "studio-a-owned",
            "OWNED_TOOL",
            task_fit=0.88,
            locality=1.0,
            privacy=1.0,
        ),
    ],
)
assert studio_a.status == "RESOLVED"
assert studio_a.job_id == job.id
assert studio_a.recommended.candidate.route_kind == "HOST_NATIVE"

studio_b = resolver.resolve(
    job,
    [
        candidate(
            "studio-b-native",
            "HOST_NATIVE",
            compatible=False,
            task_fit=1.0,
        ),
        candidate(
            "studio-b-n0te",
            "N0TE_NATIVE",
            task_fit=0.94,
            editability=0.92,
            locality=1.0,
            privacy=1.0,
        ),
        candidate(
            "studio-b-installed-unverified",
            "OWNED_TOOL",
            verified=False,
            evidence_ref=None,
            task_fit=1.0,
            user_preference=1.0,
        ),
        candidate(
            "studio-b-guided",
            "GUIDED",
            task_fit=0.70,
            locality=1.0,
            privacy=1.0,
        ),
    ],
)
assert studio_b.status == "RESOLVED"
assert studio_b.job_id == studio_a.job_id == job.id
assert studio_b.capability == studio_a.capability == job.capability
assert studio_b.recommended.candidate.route_kind == "N0TE_NATIVE"
rejected_b = {item.candidate_id: item.reason_codes for item in studio_b.rejected}
assert "INCOMPATIBLE" in rejected_b["studio-b-native"]
assert "UNVERIFIED" in rejected_b["studio-b-installed-unverified"]

private_only = resolver.resolve(
    job,
    [
        candidate(
            "cloud-provider",
            "PROVIDER",
            locality=0.10,
            privacy=0.20,
            paid=True,
            user_preference=1.0,
        ),
        candidate(
            "local-guided",
            "GUIDED",
            locality=1.0,
            privacy=1.0,
            task_fit=0.55,
            user_preference=0.0,
        ),
    ],
    ResolutionConstraints(
        min_locality=0.90,
        min_privacy=0.90,
        allow_paid=False,
        require_reversible=True,
        max_evidence_age_seconds=30,
    ),
)
assert private_only.status == "RESOLVED"
assert private_only.job_id == job.id
assert private_only.recommended.candidate.route_kind == "GUIDED"
assert "PAID_ROUTE_NOT_ALLOWED" in private_only.rejected[0].reason_codes
assert "PRIVACY_BELOW_MINIMUM" in private_only.rejected[0].reason_codes

unavailable = resolver.resolve(
    job,
    [
        candidate(
            "unknown-tool",
            "OWNED_TOOL",
            verified=False,
            evidence_ref=None,
            user_preference=1.0,
        )
    ],
)
assert unavailable.status == "UNAVAILABLE"
assert unavailable.recommended is None
assert "UNVERIFIED" in unavailable.reason_codes

identical_native = candidate("tie-b", "HOST_NATIVE", brand="Native Brand")
identical_provider = candidate("tie-a", "PROVIDER", brand="Provider Brand")
tie = resolver.resolve(job, [identical_native, identical_provider])
assert tie.candidate_ids == ("tie-a", "tie-b")
assert tie.recommended.score == tie.fallbacks[0].score
assert "SCORE_TIE_BROKEN_BY_CANDIDATE_ID" in tie.reason_codes
assert all(
    part.attribute not in {"route_kind", "brand", "display_name"}
    for part in tie.recommended.score_breakdown
)
assert resolver.resolve(job, [identical_native, identical_provider]) == tie

print(
    "CORE-03A CONSUMER SMOKE: GREEN: one N0TEable job kept its identity across native, N0TE, guided and unavailable studio outcomes; unverified/incompatible routes were rejected and no host/brand bonus existed"
)
