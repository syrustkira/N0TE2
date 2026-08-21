#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "DAW-00" or state.get("active_increment") != "DAW-00B":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.hosts import HostRuntimeIdentity  # noqa: E402
from n0te2.memory import HeadquartersMemory  # noqa: E402
from n0te2.workspace import WorkspaceError  # noqa: E402


def runtime(family="ABLETON_LIVE", version="12.1", edition="Suite"):
    return HostRuntimeIdentity.from_runtime_labels(
        host_family=family,
        version=version,
        edition=edition,
        os_name="Darwin",
        machine="arm64",
    )


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    hq = HeadquartersMemory.create(root, "Artist")
    profile_id = hq.store.profile_id
    song = hq.store.create_song("Workspace Identity Song")

    workspace = hq.workspaces.create(
        song.id,
        runtime=runtime(),
        location_ref="file:///studio/song/project-v1",
        display_name="Project V1",
    )
    workspace_id = workspace.id

    # Rename/move and host-version change are observations, not identity changes.
    moved = hq.workspaces.reconcile_existing(
        workspace_id,
        song_id=song.id,
        relation="SAME_OR_MOVED",
        runtime=runtime(version="12.2"),
        location_ref="file:///archive/song/project-renamed",
        display_name="Project Renamed",
    )
    assert moved.id == workspace_id

    # SAVE AS is intentionally ambiguous until the artist/adapter relation is explicit.
    try:
        hq.workspaces.reconcile_existing(
            workspace_id,
            song_id=song.id,
            relation="SAVE_AS",
            runtime=runtime(),
            location_ref="file:///archive/song/save-as",
        )
    except WorkspaceError:
        pass
    else:
        raise AssertionError("ambiguous Save As silently reused workspace identity")

    fork = hq.workspaces.derive(
        workspace_id,
        song_id=song.id,
        relation="FORK",
        runtime=runtime("REAPER", "7.2", "Standard"),
        location_ref="file:///archive/song/fork.rpp",
    )
    assert fork.id != workspace_id
    assert fork.source_workspace_id == workspace_id
    assert fork.source_relation == "FORK"

    # The original location is historical after the move and may now belong to a new project.
    reused = hq.workspaces.create(
        song.id,
        runtime=runtime(),
        location_ref="file:///studio/song/project-v1",
        display_name="New Project At Old Path",
    )
    assert reused.id != workspace_id
    assert hq.workspaces.current_candidates_at_location(
        "file:///studio/song/project-v1"
    ) == (reused,)

    before = hq.store._conn.total_changes
    original_state = hq.workspaces.state(workspace_id)
    hq.workspaces.history(workspace_id)
    hq.workspaces.current_candidates_at_location("file:///archive/song/project-renamed")
    assert hq.store._conn.total_changes == before
    assert original_state.current_observation.location_ref == "file:///archive/song/project-renamed"

    hq.close()
    hq = HeadquartersMemory.open(root, profile_id)
    reopened = hq.workspaces.state(workspace_id)
    assert reopened.workspace.id == workspace_id
    assert reopened.current_observation.runtime_identity["version"] == "12.2"
    assert hq.workspaces.get(fork.id).source_workspace_id == workspace_id
    hq.close()

print(
    "DAW-00B CONSUMER SMOKE: GREEN: project move/rename preserved workspace identity, ambiguous Save As was refused, explicit fork created distinct lineage, an old path was safely reusable after the move, and workspace history survived restart without path identity or cross-host guessing"
)
