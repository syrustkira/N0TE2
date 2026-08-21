#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "DAW-00" or state.get("active_increment") != "DAW-00C":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.focus import FocusDimension, FocusUncertainError  # noqa: E402
from n0te2.hosts import HostRuntimeIdentity  # noqa: E402
from n0te2.memory import HeadquartersMemory  # noqa: E402


def runtime(version="12.1"):
    return HostRuntimeIdentity.from_runtime_labels(
        host_family="ABLETON_LIVE",
        version=version,
        edition="Suite",
        os_name="Darwin",
        machine="arm64",
    )


with tempfile.TemporaryDirectory() as temp:
    hq = HeadquartersMemory.create(Path(temp), "Artist")
    song = hq.store.create_song("FocusContext Song")
    workspace = hq.workspaces.create(
        song.id,
        runtime=runtime(),
        location_ref="file:///song/project",
    )

    context = hq.focus.capture(
        workspace.id,
        song_id=song.id,
        runtime=runtime(),
        observation_evidence_ref="host-selection-read:1",
        dimensions=(
            FocusDimension("TRACK", "OBSERVED_EXACT", ("track:2",), "host:track:2"),
            FocusDimension("CLIP_REGION", "OBSERVED_EXACT", ("region:chorus",), "host:region:chorus"),
            FocusDimension("DEVICE_PLUGIN", "UNKNOWN", (), "host:no-device-selection"),
        ),
    )
    assert hq.focus.exact_refs(context, "TRACK") == ("track:2",)
    assert hq.focus.exact_refs(context, "CLIP_REGION") == ("region:chorus",)

    try:
        hq.focus.require_exact(context, "DEVICE_PLUGIN")
    except FocusUncertainError:
        pass
    else:
        raise AssertionError("unknown device focus was guessed into an exact target")

    ambiguous = hq.focus.capture(
        workspace.id,
        song_id=song.id,
        runtime=runtime(),
        observation_evidence_ref="host-selection-read:2",
        dimensions=(
            FocusDimension(
                "TRACK", "OBSERVED_AMBIGUOUS", ("track:2", "track:3"), "host:ambiguous-track"
            ),
        ),
    )
    try:
        hq.focus.require_exact(ambiguous, "TRACK")
    except FocusUncertainError:
        pass
    else:
        raise AssertionError("ambiguous track focus was guessed into an exact target")

    before = hq.store._conn.total_changes
    hq.focus.validate_current(context)
    hq.focus.require_exact(context, "TRACK")
    assert hq.store._conn.total_changes == before

    # Any new workspace observation invalidates the old focus snapshot.
    hq.workspaces.reconcile_existing(
        workspace.id,
        song_id=song.id,
        relation="SAME_OR_MOVED",
        runtime=runtime(version="12.2"),
        location_ref="file:///song/project",
    )
    try:
        hq.focus.validate_current(context)
    except FocusUncertainError as exc:
        assert exc.reason == "STALE_WORKSPACE"
    else:
        raise AssertionError("stale focus survived a workspace/runtime observation change")
    hq.close()

print(
    "DAW-00C CONSUMER SMOKE: GREEN: exact current track/region focus resolved, unknown and ambiguous targets were refused, focus reads were write-free, and the prior context became stale immediately after the workspace/runtime observation changed"
)
