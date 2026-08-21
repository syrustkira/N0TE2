#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "APP-01" or state.get("active_increment") != "APP-01A":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.app_runtime import ApplicationRuntime, ApplicationRuntimeError  # noqa: E402
from n0te2.instance import InstanceLeaseManager, ProcessIdentity  # noqa: E402
from n0te2.memory import HeadquartersMemory  # noqa: E402
from n0te2.platforms import PlatformEnvironment  # noqa: E402


class Probe:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, process: ProcessIdentity, status: str) -> None:
        self.values[process.fingerprint] = status

    def status(self, process: ProcessIdentity) -> str:
        return self.values.get(process.fingerprint, "UNKNOWN")


def process(pid: int, token: str) -> ProcessIdentity:
    return ProcessIdentity.from_start_token(
        PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
        pid=pid,
        start_token=token,
    )


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    data_root = root / "data"
    state_root = root / "state"

    bootstrap = HeadquartersMemory.create(data_root, "Consumer Runtime Artist")
    profile_id = bootstrap.store.profile_id
    bootstrap.close()

    owner_process = process(5001, "consumer-owner")
    foreign_process = process(5002, "consumer-foreign")
    probe = Probe()

    owner = ApplicationRuntime(data_root=data_root, state_root=state_root)
    started = owner.launch(profile_id=profile_id, process=owner_process, probe=probe)
    assert started.status == "STARTED"
    assert owner.state == "RUNNING"

    song = owner.headquarters.store.create_song("Quit Means Quit")
    version = owner.headquarters.store.create_version(
        song.id,
        label="Durable before quit",
    )
    assert owner.headquarters.store.active_song().id == song.id
    assert owner.headquarters.store.active_song().current_version_id == version.id

    duplicate_same_runtime = owner.launch(
        profile_id=profile_id,
        process=owner_process,
        probe=probe,
    )
    assert duplicate_same_runtime.status == "ALREADY_RUNNING"

    same_process_reopen = ApplicationRuntime(data_root=data_root, state_root=state_root)
    reopen_result = same_process_reopen.launch(
        profile_id=profile_id,
        process=owner_process,
        probe=probe,
    )
    assert reopen_result.status == "REOPEN_EXISTING"
    assert same_process_reopen.state == "STOPPED"

    probe.set(owner_process, "ALIVE")
    foreign = ApplicationRuntime(data_root=data_root, state_root=state_root)
    held = foreign.launch(
        profile_id=profile_id,
        process=foreign_process,
        probe=probe,
    )
    assert held.status == "HELD_BY_OTHER"
    assert foreign.state == "STOPPED"

    stopped = owner.quit()
    assert stopped.status == "STOPPED"
    assert owner.state == "STOPPED"
    assert InstanceLeaseManager(state_root).inspect(profile_id) is None
    try:
        _ = owner.headquarters
    except ApplicationRuntimeError:
        pass
    else:
        raise AssertionError("Headquarters remained accessible after explicit quit")

    relaunched = ApplicationRuntime(data_root=data_root, state_root=state_root)
    restarted = relaunched.launch(
        profile_id=profile_id,
        process=owner_process,
        probe=probe,
    )
    assert restarted.status == "STARTED"
    resumed = relaunched.headquarters.store.active_song()
    assert resumed is not None
    assert resumed.id == song.id
    assert resumed.title == "Quit Means Quit"
    assert resumed.current_version_id == version.id
    assert relaunched.headquarters.store.get_version(version.id) == version
    assert relaunched.quit().status == "STOPPED"
    assert InstanceLeaseManager(state_root).inspect(profile_id) is None

    public = {
        name
        for name in dir(ApplicationRuntime)
        if not name.startswith("_") and callable(getattr(ApplicationRuntime, name))
    }
    assert public == {"launch", "quit"}
    assert not (
        {
            "install",
            "update",
            "rollback",
            "uninstall",
            "kill",
            "terminate",
            "open_browser",
            "launch_window",
            "spawn_daemon",
        }
        & public
    )

print(
    "APP-01A CONSUMER SMOKE: GREEN: a real profile launched through the canonical lease into real Headquarters state, created a durable Song/version, duplicate same-runtime launch stayed idempotent, same-process reopen refused a second Headquarters, a verified-live foreign owner could not steal the profile, explicit quit closed Headquarters and released the lease, relaunch restored the exact active Song/version from disk, and the runtime exposed only launch/quit rather than installer/updater/browser/kill behavior"
)
