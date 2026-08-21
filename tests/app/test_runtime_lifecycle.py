from __future__ import annotations

from pathlib import Path

import pytest

from n0te2.app_runtime import ApplicationRuntime, ApplicationRuntimeError
from n0te2.instance import InstanceLeaseManager, ProcessIdentity
from n0te2.memory import HeadquartersMemory
from n0te2.platforms import PlatformEnvironment


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


def create_profile(data_root: Path) -> str:
    headquarters = HeadquartersMemory.create(data_root, "Runtime Test Artist")
    try:
        return headquarters.store.profile_id
    finally:
        headquarters.close()


def test_real_song_and_version_survive_explicit_quit_and_relaunch(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    profile_id = create_profile(data_root)
    proc = process(101, "boot:one")
    probe = Probe()

    first = ApplicationRuntime(data_root=data_root, state_root=state_root)
    started = first.launch(profile_id=profile_id, process=proc, probe=probe)
    assert started.status == "STARTED"
    song = first.headquarters.store.create_song("Resume Me")
    version = first.headquarters.store.create_version(song.id, label="First durable idea")
    assert first.headquarters.store.active_song() == first.headquarters.store.get_song(song.id)
    assert first.headquarters.store.active_song().current_version_id == version.id

    stopped = first.quit()
    assert stopped.status == "STOPPED"
    assert first.state == "STOPPED"
    assert InstanceLeaseManager(state_root).inspect(profile_id) is None
    with pytest.raises(ApplicationRuntimeError):
        _ = first.headquarters

    second = ApplicationRuntime(data_root=data_root, state_root=state_root)
    restarted = second.launch(profile_id=profile_id, process=proc, probe=probe)
    assert restarted.status == "STARTED"
    resumed = second.headquarters.store.active_song()
    assert resumed is not None
    assert resumed.id == song.id
    assert resumed.title == "Resume Me"
    assert resumed.current_version_id == version.id
    assert second.headquarters.store.get_version(version.id) == version
    assert second.quit().status == "STOPPED"


def test_same_runtime_duplicate_launch_does_not_reopen_headquarters(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    profile_id = create_profile(data_root)
    proc = process(102, "same-runtime")
    probe = Probe()
    opens: list[str] = []

    def opener(root: str | Path, profile: str) -> HeadquartersMemory:
        opens.append(profile)
        return HeadquartersMemory.open(root, profile)

    runtime = ApplicationRuntime(
        data_root=data_root,
        state_root=state_root,
        memory_opener=opener,
    )
    assert runtime.launch(profile_id=profile_id, process=proc, probe=probe).status == "STARTED"
    repeated = runtime.launch(profile_id=profile_id, process=proc, probe=probe)
    assert repeated.status == "ALREADY_RUNNING"
    assert opens == [profile_id]
    assert runtime.quit().status == "STOPPED"


def test_second_runtime_same_process_returns_reopen_existing_without_second_database_open(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    profile_id = create_profile(data_root)
    proc = process(103, "same-process")
    probe = Probe()
    opens: list[str] = []

    def opener(root: str | Path, profile: str) -> HeadquartersMemory:
        opens.append(profile)
        return HeadquartersMemory.open(root, profile)

    owner = ApplicationRuntime(
        data_root=data_root,
        state_root=state_root,
        memory_opener=opener,
    )
    contender = ApplicationRuntime(
        data_root=data_root,
        state_root=state_root,
        memory_opener=opener,
    )
    assert owner.launch(profile_id=profile_id, process=proc, probe=probe).status == "STARTED"
    reopened = contender.launch(profile_id=profile_id, process=proc, probe=probe)
    assert reopened.status == "REOPEN_EXISTING"
    assert contender.state == "STOPPED"
    assert opens == [profile_id]
    assert owner.quit().status == "STOPPED"


@pytest.mark.parametrize(
    ("owner_status", "expected"),
    [("ALIVE", "HELD_BY_OTHER"), ("UNKNOWN", "UNCERTAIN")],
)
def test_foreign_live_or_uncertain_owner_never_opens_second_headquarters(
    tmp_path: Path,
    owner_status: str,
    expected: str,
) -> None:
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    profile_id = create_profile(data_root)
    owner_process = process(104, "owner")
    challenger_process = process(105, "challenger")
    probe = Probe()

    owner = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert owner.launch(profile_id=profile_id, process=owner_process, probe=probe).status == "STARTED"
    probe.set(owner_process, owner_status)

    opens: list[str] = []

    def opener(root: str | Path, profile: str) -> HeadquartersMemory:
        opens.append(profile)
        return HeadquartersMemory.open(root, profile)

    challenger = ApplicationRuntime(
        data_root=data_root,
        state_root=state_root,
        memory_opener=opener,
    )
    result = challenger.launch(
        profile_id=profile_id,
        process=challenger_process,
        probe=probe,
    )
    assert result.status == expected
    assert challenger.state == "STOPPED"
    assert opens == []
    assert owner.quit().status == "STOPPED"


def test_verified_dead_owner_is_replaced_with_previous_lease_receipt(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    profile_id = create_profile(data_root)
    dead_process = process(106, "dead-owner")
    replacement_process = process(107, "replacement")
    probe = Probe()

    stale_manager = InstanceLeaseManager(state_root)
    stale = stale_manager.acquire(profile_id, dead_process, probe)
    assert stale.status == "ACQUIRED"
    probe.set(dead_process, "DEAD")

    runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)
    result = runtime.launch(
        profile_id=profile_id,
        process=replacement_process,
        probe=probe,
    )
    assert result.status == "STARTED"
    assert result.previous_lease == stale.lease
    assert runtime.quit().status == "STOPPED"


def test_headquarters_open_failure_does_not_strand_new_lease(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    profile_id = create_profile(data_root)
    proc = process(108, "open-failure")
    probe = Probe()

    def fail_open(root: str | Path, profile: str) -> HeadquartersMemory:
        raise RuntimeError("synthetic open failure")

    runtime = ApplicationRuntime(
        data_root=data_root,
        state_root=state_root,
        memory_opener=fail_open,
    )
    result = runtime.launch(profile_id=profile_id, process=proc, probe=probe)
    assert result.status == "START_FAILED"
    assert runtime.state == "STOPPED"
    assert InstanceLeaseManager(state_root).inspect(profile_id) is None


def test_quit_is_idempotent_after_success(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    profile_id = create_profile(data_root)
    proc = process(109, "quit-idempotent")
    probe = Probe()
    runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)

    assert runtime.launch(profile_id=profile_id, process=proc, probe=probe).status == "STARTED"
    assert runtime.quit().status == "STOPPED"
    assert runtime.quit().status == "ALREADY_STOPPED"


def test_runtime_public_surface_does_not_hide_install_update_kill_or_ui_actions() -> None:
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
            "download",
            "kill",
            "terminate",
            "launch_window",
            "open_browser",
            "spawn_daemon",
        }
        & public
    )
