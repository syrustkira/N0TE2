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

if active != "CORE-03" or increment != "CORE-03E":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import (  # noqa: E402
    CapabilityCandidate,
    N0TEableJob,
    SemanticToolProfile,
    StudioCapabilityProfile,
    ToolCapabilityBinding,
    ToolEndpoint,
    ToolParameterBinding,
    ToolStateBinding,
)


def owned_candidate(candidate_id: str, capability: str, **overrides):
    values = dict(
        candidate_id=candidate_id,
        route_kind="OWNED_TOOL",
        capability=capability,
        display_name=candidate_id,
        brand=None,
        verified=True,
        compatible=True,
        evidence_ref=f"smoke:{candidate_id}",
        evidence_age_seconds=10,
        task_fit=0.90,
        editability=0.90,
        locality=1.0,
        privacy=1.0,
        latency=0.90,
        reversibility=1.0,
        cost_efficiency=1.0,
        portability=0.80,
        user_preference=0.50,
        paid=False,
    )
    values.update(overrides)
    return CapabilityCandidate(**values)


vst3 = ToolEndpoint(
    endpoint_id="endpoint:vst3",
    format_kind="VST3",
    native_identity="vendor.example.compressor.vst3",
    evidence_ref="smoke:endpoint:vst3",
)
au = ToolEndpoint(
    endpoint_id="endpoint:au",
    format_kind="AU",
    native_identity="aufx:Vndr:Comp",
    evidence_ref="smoke:endpoint:au",
)

tool = SemanticToolProfile(
    tool_id="tool:example-compressor",
    display_name="Example Compressor",
    endpoints=(vst3, au),
    capabilities=(
        ToolCapabilityBinding(
            endpoint_id=vst3.endpoint_id,
            candidate=owned_candidate(
                "candidate:compress:vst3",
                "dynamics.compress",
                display_name="Example Compressor VST3",
                brand="Example Vendor",
                task_fit=0.94,
            ),
        ),
        ToolCapabilityBinding(
            endpoint_id=au.endpoint_id,
            candidate=owned_candidate(
                "candidate:compress:au",
                "dynamics.compress",
                display_name="Example Compressor AU",
                brand="Example Vendor",
                task_fit=0.90,
            ),
        ),
    ),
    parameters=(
        ToolParameterBinding(
            endpoint_id=vst3.endpoint_id,
            semantic_key="mix.wet_dry",
            native_parameter_ref="param:12",
            readable=True,
            writable=True,
            evidence_ref="smoke:param:vst3:mix",
        ),
        ToolParameterBinding(
            endpoint_id=au.endpoint_id,
            semantic_key="mix.wet_dry",
            native_parameter_ref="kAudioUnitParameter_WetDryMix",
            readable=True,
            writable=False,
            evidence_ref="smoke:param:au:mix",
        ),
    ),
    state_bindings=(
        ToolStateBinding(
            endpoint_id=vst3.endpoint_id,
            readable=True,
            writable=True,
            evidence_ref="smoke:state:vst3",
        ),
        ToolStateBinding(
            endpoint_id=au.endpoint_id,
            readable=True,
            writable=False,
            evidence_ref="smoke:state:au",
        ),
    ),
)

# One semantic identity spans multiple format/native endpoints.
assert tool.tool_id == "tool:example-compressor"
assert tuple(item.endpoint_id for item in tool.endpoints) == (
    "endpoint:au", "endpoint:vst3"
)
assert {item.format_kind for item in tool.endpoints} == {"AU", "VST3"}
assert tool.endpoint("endpoint:au").native_identity != tool.endpoint("endpoint:vst3").native_identity

# Semantic parameter/state facts remain endpoint-specific and explicit.
mix_bindings = tool.parameter_bindings_for("mix.wet_dry")
assert len(mix_bindings) == 2
assert {item.endpoint_id: item.native_parameter_ref for item in mix_bindings} == {
    "endpoint:au": "kAudioUnitParameter_WetDryMix",
    "endpoint:vst3": "param:12",
}
assert tool.state_for_endpoint("endpoint:vst3").writable is True
assert tool.state_for_endpoint("endpoint:au").writable is False

# Only explicit existing OWNED_TOOL candidates feed the existing resolver.
studio = StudioCapabilityProfile.build(
    environment_id="studio:owned-tools",
    candidates=tool.candidates(),
)
job = N0TEableJob(
    id="job:compress",
    capability="dynamics.compress",
    description="Compress a source using a legitimate owned-tool route",
)
resolved = studio.resolve(job)
assert resolved.status == "RESOLVED"
assert resolved.recommended.candidate.route_kind == "OWNED_TOOL"
assert resolved.recommended.candidate.candidate_id == "candidate:compress:vst3"

# Endpoint/install/display-name presence alone produces zero semantic capability.
famous_endpoint_only = SemanticToolProfile(
    tool_id="tool:famous-installed",
    display_name="Famous Magic Compressor",
    endpoints=(
        ToolEndpoint(
            endpoint_id="endpoint:famous",
            format_kind="VST3",
            native_identity="FamousVendor.MagicCompressor",
            evidence_ref="smoke:endpoint:famous",
        ),
    ),
)
assert famous_endpoint_only.candidates() == ()
famous_studio = StudioCapabilityProfile.build(
    environment_id="studio:famous",
    candidates=famous_endpoint_only.candidates(),
)
unavailable = famous_studio.resolve(job)
assert unavailable.status == "UNAVAILABLE"
assert "NO_CANDIDATES" in unavailable.reason_codes

# An explicit but unverified candidate remains represented and rejected by CORE-03A.
unverified_tool = SemanticToolProfile(
    tool_id="tool:unverified",
    display_name="Unverified Installed Compressor",
    endpoints=(vst3,),
    capabilities=(
        ToolCapabilityBinding(
            endpoint_id=vst3.endpoint_id,
            candidate=owned_candidate(
                "candidate:unverified",
                "dynamics.compress",
                verified=False,
                evidence_ref=None,
                task_fit=1.0,
                user_preference=1.0,
            ),
        ),
    ),
)
unverified_studio = StudioCapabilityProfile.build(
    environment_id="studio:unverified",
    candidates=unverified_tool.candidates(),
)
unverified_result = unverified_studio.resolve(job)
assert unverified_result.status == "UNAVAILABLE"
assert "UNVERIFIED" in unverified_result.reason_codes

# Reads are pure and deterministic. No scan, load, parameter write or state I/O occurs here.
assert tool.candidates() == tool.candidates()
assert tool.parameter_bindings_for("mix.wet_dry") == mix_bindings
assert tool.state_for_endpoint("endpoint:vst3").writable is True

print(
    "CORE-03E CONSUMER SMOKE: GREEN: one semantic Tool identity spanned VST3/AU endpoints with explicit capability/parameter/state evidence; installed names created no capability, unverified facts stayed unusable, and no discovery or hosting occurred"
)
