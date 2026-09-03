from __future__ import annotations

from pathlib import Path

import pytest

from n0te2.app_runtime import ApplicationRuntime, ApplicationRuntimeError
from n0te2.instance import ProcessIdentity
from n0te2.memory import HeadquartersMemory
from n0te2.platforms import PlatformEnvironment


class Probe:
    def status(self, process: ProcessIdentity) -> str:
        return "UNKNOWN"


def process(pid: int, token: str) -> ProcessIdentity:
    return ProcessIdentity.from_start_token(
        PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
        pid=pid,
        start_token=token,
    )


def test_cold_start_reconstructs_same_artist_song_version_and_context(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"

    seeded = HeadquartersMemory.create(data_root, "TellMeN0TE Test")
    try:
        profile_id = seeded.store.profile_id
        artist = seeded.store.artist()
        song = seeded.store.create_song("Cold Start Song")
        version = seeded.store.create_version(song.id, label="Durable working version")
    finally:
        seeded.close()

    first = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert first.launch(
        profile_id=profile_id,
        process=process(201, "cold-start:first"),
        probe=Probe(),
    ).status == "STARTED"

    before_changes = first.headquarters.store._conn.total_changes
    first_state = first.continuation_state()
    after_changes = first.headquarters.store._conn.total_changes

    assert after_changes == before_changes
    assert first_state.profile_id == profile_id
    assert first_state.artist_id == artist.id
    assert first_state.artist_name == "TellMeN0TE Test"
    assert first_state.song_id == song.id
    assert first_state.song_title == "Cold Start Song"
    assert first_state.current_version_id == version.id
    assert first_state.approved_version_id is None
    assert first_state.context_projection is not None
    assert first_state.context_projection["purpose"] == "COLD_START_CONTINUATION"
    assert first_state.context_projection["authority_ceiling"] == "READ_ONLY_CONTEXT"
    first_digest = first_state.context_projection["source_digest"]

    assert first.quit().status == "STOPPED"

    second = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert second.launch(
        profile_id=profile_id,
        process=process(202, "cold-start:second"),
        probe=Probe(),
    ).status == "STARTED"
    second_state = second.continuation_state()

    assert second_state.profile_id == first_state.profile_id
    assert second_state.artist_id == first_state.artist_id
    assert second_state.artist_name == first_state.artist_name
    assert second_state.song_id == first_state.song_id
    assert second_state.song_title == first_state.song_title
    assert second_state.current_version_id == first_state.current_version_id
    assert second_state.approved_version_id == first_state.approved_version_id
    assert second_state.context_projection is not None
    assert second_state.context_projection["source_digest"] == first_digest
    assert second.quit().status == "STOPPED"


def test_continuation_state_without_active_song_keeps_artist_identity(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    seeded = HeadquartersMemory.create(data_root, "Blank Headquarters")
    try:
        profile_id = seeded.store.profile_id
        artist_id = seeded.store.artist().id
    finally:
        seeded.close()

    runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert runtime.launch(
        profile_id=profile_id,
        process=process(203, "cold-start:blank"),
        probe=Probe(),
    ).status == "STARTED"
    state = runtime.continuation_state()

    assert state.profile_id == profile_id
    assert state.artist_id == artist_id
    assert state.artist_name == "Blank Headquarters"
    assert state.song_id is None
    assert state.song_title is None
    assert state.current_version_id is None
    assert state.approved_version_id is None
    assert state.context_projection is None
    assert runtime.quit().status == "STOPPED"


def test_continuation_state_requires_running_runtime(tmp_path: Path) -> None:
    runtime = ApplicationRuntime(
        data_root=tmp_path / "data",
        state_root=tmp_path / "state",
    )
    with pytest.raises(ApplicationRuntimeError, match="Headquarters is available only while RUNNING"):
        runtime.continuation_state()
