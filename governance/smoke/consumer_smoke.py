#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "PLATFORM-00" or state.get("active_increment") != "PLATFORM-00E":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.platforms import PlatformEnvironment  # noqa: E402
from n0te2.support import SupportTarget  # noqa: E402
from n0te2.worker import (  # noqa: E402
    WorkerCapability,
    WorkerEnvelope,
    WorkerEnvelopeError,
    WorkerIdentity,
    WorkerRequest,
    WorkerResult,
)


app_target = SupportTarget.from_runtime_labels(os_name="Darwin", machine="arm64")
arm_worker = WorkerIdentity(
    "plugin_worker",
    PlatformEnvironment.from_runtime_labels("Darwin", "arm64"),
    "a" * 64,
)
arm_capability = WorkerCapability(
    "cap:native-vst3",
    "tool.process",
    "VST3",
    "NATIVE_ONLY",
    ("ARM64",),
)
arm_request = WorkerRequest(
    "req:native",
    arm_worker.fingerprint,
    app_target.fingerprint,
    "tool.process",
    "VST3",
    "ARM64",
    "b" * 64,
    1500,
)
native_route = WorkerEnvelope.plan(
    worker=arm_worker,
    capability=arm_capability,
    request=arm_request,
    target=app_target,
)
assert native_route.foreign_architecture is False
assert native_route.execution_mode == "NATIVE_ONLY"

# The app remains ARM64. A foreign x86_64 workload is represented only by an
# explicitly isolated x86_64 worker route on the same OS family.
x64_worker = WorkerIdentity(
    "plugin_worker",
    PlatformEnvironment.from_runtime_labels("Darwin", "amd64"),
    "c" * 64,
)
x64_capability = WorkerCapability(
    "cap:isolated-vst3-x64",
    "tool.process",
    "VST3",
    "ISOLATED_FOREIGN_ARCH",
    ("X86_64",),
)
x64_request = WorkerRequest(
    "req:foreign",
    x64_worker.fingerprint,
    app_target.fingerprint,
    "tool.process",
    "VST3",
    "X86_64",
    "d" * 64,
    1500,
)
bridge_route = WorkerEnvelope.plan(
    worker=x64_worker,
    capability=x64_capability,
    request=x64_request,
    target=app_target,
)
assert bridge_route.foreign_architecture is True
assert bridge_route.execution_mode == "ISOLATED_FOREIGN_ARCH"
assert app_target.architecture == "ARM64"
assert x64_worker.platform.architecture == "X86_64"

# The same foreign worker cannot be laundered through a native-only claim.
try:
    WorkerEnvelope.plan(
        worker=x64_worker,
        capability=WorkerCapability(
            "cap:false-native",
            "tool.process",
            "VST3",
            "NATIVE_ONLY",
            ("X86_64",),
        ),
        request=x64_request,
        target=app_target,
    )
except WorkerEnvelopeError:
    pass
else:
    raise AssertionError("foreign architecture was accepted as NATIVE_ONLY")

success = WorkerResult(
    request_fingerprint=x64_request.fingerprint,
    worker_fingerprint=x64_worker.fingerprint,
    state="SUCCEEDED",
    evidence_ref="worker:verified-result",
    result_fingerprint="e" * 64,
    receipt_ref="worker:receipt:1",
)
assert WorkerEnvelope.validate_result(
    worker=x64_worker,
    request=x64_request,
    result=success,
).state == "SUCCEEDED"

for non_success in ("FAILED", "CRASHED", "TIMED_OUT", "UNKNOWN"):
    result = WorkerResult(
        x64_request.fingerprint,
        x64_worker.fingerprint,
        non_success,
        f"worker:{non_success.lower()}",
    )
    assert WorkerEnvelope.validate_result(
        worker=x64_worker,
        request=x64_request,
        result=result,
    ).state == non_success

stale_request = WorkerRequest(
    "req:other",
    x64_worker.fingerprint,
    app_target.fingerprint,
    "tool.process",
    "VST3",
    "X86_64",
    "d" * 64,
    1500,
)
try:
    WorkerEnvelope.validate_result(
        worker=x64_worker,
        request=stale_request,
        result=success,
    )
except WorkerEnvelopeError:
    pass
else:
    raise AssertionError("result for another request was accepted")

public = {
    name
    for name in dir(WorkerEnvelope)
    if not name.startswith("_") and callable(getattr(WorkerEnvelope, name))
}
assert public == {"plan", "validate_result"}
assert not (
    {
        "spawn",
        "subprocess",
        "ipc",
        "load",
        "process_audio",
        "kill",
        "install",
        "execute",
        "connect",
    }
    & public
)

print(
    "PLATFORM-00E CONSUMER SMOKE: GREEN: native ARM64 work stayed native, an x86_64 workload on the ARM64 app was accepted only through an explicit isolated same-OS x86_64 worker while the app target remained ARM64, pretending that worker was native was rejected, success required exact result/receipt evidence, failure/crash/timeout/unknown stayed distinct, stale results were rejected, and the worker envelope exposed no process/plugin execution verb"
)
