#!/usr/bin/env python3
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "DAW-00" or state.get("active_increment") != "DAW-00A":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.hosts import (  # noqa: E402
    CORE_HOST_FAMILIES,
    HOST_FAMILIES,
    HostIdentityError,
    HostRuntimeIdentity,
    assert_identity_contract_has_no_priority_fields,
)

assert HOST_FAMILIES == (
    "ABLETON_LIVE",
    "FL_STUDIO",
    "LOGIC_PRO",
    "PRO_TOOLS",
    "STUDIO_ONE",
    "REAPER",
    "GENERIC_OTHER",
)

fingerprints = {}
for family in CORE_HOST_FAMILIES:
    identity = HostRuntimeIdentity.from_runtime_labels(
        host_family=family,
        version="1.0",
        edition="STANDARD",
        os_name="Darwin",
        machine="arm64",
    )
    fingerprints[family] = identity.fingerprint
    assert identity.family == family
    assert identity.platform.os_family == "MACOS"
    assert identity.platform.architecture == "ARM64"
assert len(set(fingerprints.values())) == 6

# A display label is presentation, not product priority or runtime identity.
logic_a = HostRuntimeIdentity.from_runtime_labels(
    host_family="Logic Pro", version="11.0", edition="Standard",
    os_name="Darwin", machine="arm64", display_name="Logic Pro",
)
logic_b = HostRuntimeIdentity.from_runtime_labels(
    host_family="Apple Logic Pro", version="11.0", edition="standard",
    os_name="macOS", machine="aarch64", display_name="My Current DAW",
)
assert logic_a.fingerprint == logic_b.fingerprint

# Material runtime changes invalidate identity.
logic_version_change = HostRuntimeIdentity.from_runtime_labels(
    host_family="LOGIC_PRO", version="11.1", edition="Standard",
    os_name="Darwin", machine="arm64",
)
logic_translation_change = HostRuntimeIdentity.from_runtime_labels(
    host_family="LOGIC_PRO", version="11.0", edition="Standard",
    os_name="Darwin", machine="arm64", translation_mode="Rosetta 2",
)
assert logic_version_change.fingerprint != logic_a.fingerprint
assert logic_translation_change.fingerprint != logic_a.fingerprint

# GENERIC_OTHER is deliberate and differentiated, never an automatic fallback bucket.
bitwig = HostRuntimeIdentity.from_runtime_labels(
    host_family="GENERIC_OTHER", version="5.0", edition="Standard",
    os_name="Linux", machine="x86_64", generic_host_label="Bitwig Studio",
)
waveform = HostRuntimeIdentity.from_runtime_labels(
    host_family="GENERIC_OTHER", version="5.0", edition="Standard",
    os_name="Linux", machine="x86_64", generic_host_label="Tracktion Waveform",
)
assert bitwig.fingerprint != waveform.fingerprint
try:
    HostRuntimeIdentity.from_runtime_labels(
        host_family="Bitwig Studio", version="5.0", edition="Standard",
        os_name="Linux", machine="x86_64",
    )
except HostIdentityError:
    pass
else:
    raise AssertionError("unknown host silently became a supported/generic host identity")

assert_identity_contract_has_no_priority_fields()
field_names = {field.name.casefold() for field in dataclasses.fields(HostRuntimeIdentity)}
for forbidden in (
    "rank", "priority", "preferred", "default", "capability", "support",
    "adapter", "path", "process", "executable",
):
    assert not any(forbidden in name for name in field_names)

print(
    "DAW-00A CONSUMER SMOKE: GREEN: Ableton Live, FL Studio, Logic Pro, Pro Tools, Studio One and REAPER remained peer host identities; GENERIC_OTHER stayed explicit; cosmetic labels could not alter core identity; real runtime changes did; and the identity model contained no host priority, capability, support, path or process semantics"
)
