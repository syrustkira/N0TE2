#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "PLATFORM-00" or state.get("active_increment") != "PLATFORM-00C":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.support import (  # noqa: E402
    SupportEvidence,
    SupportTarget,
    default_architecture_targets,
    default_support_envelope,
)


targets = default_architecture_targets()
core = tuple(target for target in targets if target.policy_tier == "CORE")
extended = tuple(target for target in targets if target.policy_tier == "EXTENDED")
assert len(core) == 6
assert len(extended) == 4

initial = default_support_envelope()
assert len(initial.customer_mode_blockers()) == 6
assert all(blocker.state == "UNVERIFIED" for blocker in initial.customer_mode_blockers())

# Even perfect evidence for every EXTENDED target cannot substitute for any
# required CORE platform/architecture target.
extended_only = default_support_envelope(
    SupportEvidence(target.fingerprint, "ACCEPTED", f"accept:extended:{index}")
    for index, target in enumerate(extended)
)
assert len(extended_only.customer_mode_blockers()) == 6

mac_arm = next(
    target
    for target in core
    if target.os_family == "MACOS" and target.architecture == "ARM64"
)
windows_x64 = next(
    target
    for target in core
    if target.os_family == "WINDOWS" and target.architecture == "X86_64"
)
linux_arm = next(
    target
    for target in core
    if target.os_family == "LINUX" and target.architecture == "ARM64"
)

evidence = (
    SupportEvidence(mac_arm.fingerprint, "ACCEPTED", "accept:mac-arm64"),
    SupportEvidence(
        windows_x64.fingerprint,
        "LEGACY_ACCEPTED",
        "accept:windows-x64-legacy",
        upstream_limitation="Upstream OS servicing limitation remains visible",
    ),
    SupportEvidence(
        linux_arm.fingerprint,
        "KNOWN_BREAK",
        "probe:linux-arm64-break",
        known_break_reason="Required package dependency unavailable in tested environment",
    ),
)
observed = default_support_envelope(evidence)
assert len(observed.customer_mode_blockers()) == 4
assert observed.status(mac_arm).state == "ACCEPTED"
assert observed.status(windows_x64).state == "LEGACY_ACCEPTED"
assert observed.status(windows_x64).upstream_limitation is not None
linux_blocker = next(
    blocker
    for blocker in observed.customer_mode_blockers()
    if blocker.target_fingerprint == linux_arm.fingerprint
)
assert linux_blocker.state == "KNOWN_BREAK"
assert "Required package dependency unavailable" in linux_blocker.reason

# Runtime aliases normalize through PLATFORM-00A and do not create divergent
# support identity merely because the OS/CPU label spelling differs.
mac_alias = SupportTarget.from_runtime_labels(os_name="Darwin", machine="aarch64")
mac_canonical = SupportTarget.from_runtime_labels(os_name="macOS", machine="arm64")
assert mac_alias == mac_canonical
assert mac_alias.fingerprint == mac_canonical.fingerprint

# There is no generic boolean that can flatten policy/evidence into a vague
# "supported" claim.
assert not hasattr(observed.status(mac_arm), "supported")
assert not hasattr(mac_arm, "supported")

print(
    "PLATFORM-00C CONSUMER SMOKE: GREEN: six CORE macOS/Windows/Linux architecture targets began as explicit UNVERIFIED customer-mode blockers, accepting every EXTENDED target did not satisfy or hide any CORE blocker, exact acceptance removed only its target, legacy acceptance preserved its upstream limitation, a named CORE break stayed visible and blocking, runtime aliases converged through canonical platform identity, and no generic supported boolean could flatten target policy into acceptance truth"
)
