#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "PLATFORM-00" or state.get("active_increment") != "PLATFORM-00B":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.instance import (  # noqa: E402
    InstanceLeaseManager,
    InstanceLeaseOwnershipError,
    ProcessIdentity,
)
from n0te2.platforms import PlatformEnvironment, target_tier  # noqa: E402


class Probe:
    def __init__(self):
        self.values = {}

    def set(self, process, status):
        self.values[process.fingerprint] = status

    def status(self, process):
        return self.values.get(process.fingerprint, "UNKNOWN")


def platform():
    return PlatformEnvironment(
        os_family="LINUX",
        architecture="X86_64",
        raw_os_name="LINUX",
        raw_machine="X86_64",
        target_tier=target_tier("LINUX", "X86_64"),
    )


def process(pid, token):
    return ProcessIdentity.from_start_token(platform(), pid=pid, start_token=token)


with tempfile.TemporaryDirectory() as temp:
    manager = InstanceLeaseManager(Path(temp).resolve())
    probe = Probe()
    owner = process(100, "start:owner")
    pid_reused = process(100, "start:reused-pid")
    challenger = process(200, "start:challenger")

    first = manager.acquire("profile_x", owner, probe)
    assert first.status == "ACQUIRED"
    repeated = manager.acquire("profile_x", owner, probe)
    assert repeated.status == "ALREADY_OWNED"
    assert repeated.lease == first.lease
    assert owner.pid == pid_reused.pid
    assert owner.fingerprint != pid_reused.fingerprint

    probe.set(owner, "ALIVE")
    live_refusal = manager.acquire("profile_x", challenger, probe)
    assert live_refusal.status == "HELD_BY_OTHER"
    assert manager.inspect("profile_x") == first.lease

    probe.set(owner, "UNKNOWN")
    uncertain = manager.acquire("profile_x", challenger, probe)
    assert uncertain.status == "UNCERTAIN"
    assert manager.inspect("profile_x") == first.lease

    probe.set(owner, "DEAD")
    replacement = manager.acquire("profile_x", challenger, probe)
    assert replacement.status == "REPLACED_STALE"
    assert replacement.previous_lease == first.lease
    assert manager.inspect("profile_x") == replacement.lease

    try:
        manager.release(
            "profile_x",
            process=owner,
            lease_nonce=replacement.lease.lease_nonce,
        )
    except InstanceLeaseOwnershipError:
        pass
    else:
        raise AssertionError("old process identity released a replacement lease")

    manager.release(
        "profile_x",
        process=challenger,
        lease_nonce=replacement.lease.lease_nonce,
    )
    assert manager.inspect("profile_x") is None

    public = {
        name
        for name in dir(InstanceLeaseManager)
        if not name.startswith("_") and callable(getattr(InstanceLeaseManager, name))
    }
    assert not ({"kill", "launch", "terminate", "signal", "connect"} & public)

print(
    "PLATFORM-00B CONSUMER SMOKE: GREEN: one process acquired the profile lease idempotently, PID reuse remained a different identity, a live foreign owner blocked takeover, UNKNOWN liveness failed closed, only verified DEAD ownership was archived/replaced with prior-lease receipt, old ownership could not release the replacement, exact ownership released cleanly, and the lease manager exposes no process-kill or launch verb"
)
