#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "APP-01" or state.get("active_increment") != "APP-01B":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.app_runtime import ApplicationRuntime  # noqa: E402
from n0te2.instance import ProcessIdentity  # noqa: E402
from n0te2.memory import HeadquartersMemory  # noqa: E402
from n0te2.platforms import PlatformEnvironment  # noqa: E402
from n0te2.profiles import ApplicationProfiles  # noqa: E402


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
    proc = process(6001, "fresh-install-owner")
    probe = Probe()

    profiles = ApplicationProfiles(data_root=data_root, state_root=state_root)
    empty = profiles.resolve()
    assert empty.state == "NEEDS_CREATION"
    assert empty.profiles == ()
    assert empty.issues == ()

    created = profiles.resolve(
        artist_name="Fresh Install Artist",
        process=proc,
        probe=probe,
    )
    assert created.state == "CREATED"
    assert created.selected_profile_id is not None
    assert len(created.profiles) == 1
    assert created.profiles[0].artist_name == "Fresh Install Artist"
    profile_id = created.selected_profile_id

    runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)
    started = runtime.launch(profile_id=profile_id, process=proc, probe=probe)
    assert started.status == "STARTED"
    song = runtime.headquarters.store.create_song("Fresh Install Song")
    version = runtime.headquarters.store.create_version(
        song.id,
        label="First version",
    )
    assert runtime.quit().status == "STOPPED"

    # A new application-profile resolver on the same durable roots finds the
    # same Artist identity without requiring creation input again.
    rediscovered = ApplicationProfiles(
        data_root=data_root,
        state_root=state_root,
    ).resolve()
    assert rediscovered.state == "SELECTED_EXISTING"
    assert rediscovered.selected_profile_id == profile_id
    assert len(rediscovered.profiles) == 1

    relaunched = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert relaunched.launch(
        profile_id=rediscovered.selected_profile_id,
        process=proc,
        probe=probe,
    ).status == "STARTED"
    resumed = relaunched.headquarters.store.active_song()
    assert resumed is not None
    assert resumed.id == song.id
    assert resumed.title == "Fresh Install Song"
    assert resumed.current_version_id == version.id
    assert relaunched.headquarters.store.get_version(version.id) == version
    assert relaunched.quit().status == "STOPPED"

    # Once a second healthy profile exists, the application must stop guessing
    # and request an explicit profile choice.
    second = HeadquartersMemory.create(data_root, "Second Local Artist")
    second_profile_id = second.store.profile_id
    second.close()
    ambiguous = profiles.resolve()
    assert ambiguous.state == "NEEDS_SELECTION"
    assert {item.profile_id for item in ambiguous.profiles} == {
        profile_id,
        second_profile_id,
    }
    explicit = profiles.resolve(selected_profile_id=second_profile_id)
    assert explicit.state == "SELECTED_EXISTING"
    assert explicit.selected_profile_id == second_profile_id

    public = {
        name
        for name in dir(ApplicationProfiles)
        if not name.startswith("_") and callable(getattr(ApplicationProfiles, name))
    }
    assert public == {"discover", "resolve"}
    assert not (
        {
            "delete",
            "merge",
            "rename",
            "upload",
            "sync",
            "login",
            "create_account",
        }
        & public
    )

print(
    "APP-01B CONSUMER SMOKE: GREEN: a truly empty install requested Artist-profile creation, created exactly one durable local profile under bootstrap ownership, launched that profile through the real application runtime, created and persisted a Song/version, explicitly quit, rediscovered the same Artist profile on a fresh resolver, relaunched the exact Song/version, required explicit selection once a second healthy local profile existed, and exposed no destructive/cloud/account profile action"
)
