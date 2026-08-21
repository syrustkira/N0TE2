#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "DAW-00" or state.get("active_increment") != "DAW-00D":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.hosts import HostRuntimeIdentity  # noqa: E402
from n0te2.memory import HeadquartersMemory  # noqa: E402
from n0te2.shadow import HostShadowError, ShadowEventInput  # noqa: E402


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
    song = hq.store.create_song("Host Shadow Song")
    workspace = hq.workspaces.create(
        song.id,
        runtime=runtime(),
        location_ref="file:///song/project",
    )

    def binding():
        observation = hq.workspaces.state(workspace.id).current_observation
        return observation.id, observation.host_runtime_fingerprint

    def record(coverage, actor, evidence_ref, events=()):
        observation_id, fingerprint = binding()
        return hq.shadow.record_batch(
            workspace.id,
            workspace_observation_id=observation_id,
            host_runtime_fingerprint=fingerprint,
            coverage=coverage,
            actor=actor,
            evidence_ref=evidence_ref,
            verified=True,
            events=events,
        )

    assert hq.shadow.state(workspace.id).status == "EMPTY"
    try:
        record(
            "INCREMENTAL",
            "HUMAN",
            "host:delta-before-full",
            events=(ShadowEventInput("TRACK", "track:1", "mute", "SET", True),),
        )
    except HostShadowError:
        pass
    else:
        raise AssertionError("incremental Host Shadow was accepted before a FULL baseline")

    evidence_before = hq.store._conn.execute(
        "SELECT COUNT(*) FROM evidence_claims"
    ).fetchone()[0]
    full = record(
        "FULL",
        "EXTERNAL",
        "host:full-scan",
        events=(
            ShadowEventInput("TRACK", "track:1", "name", "SET", "Kick"),
            ShadowEventInput("TRACK", "track:1", "mute", "SET", False),
            ShadowEventInput("TEMPO", "song", "bpm", "SET", 120.0),
        ),
    )
    incremental = record(
        "INCREMENTAL",
        "HUMAN",
        "host:user-change",
        events=(
            ShadowEventInput(
                "TRACK", "track:1", "mute", "SET", True, "host:track-1-mute"
            ),
        ),
    )
    current = hq.shadow.require_current(workspace.id)
    assert current.baseline_batch_id == full.id
    assert current.latest_batch_id == incremental.id
    facts = {(fact.object_ref, fact.field): fact for fact in current.facts}
    assert facts[("track:1", "name")].value == "Kick"
    assert facts[("track:1", "mute")].value is True
    assert facts[("track:1", "mute")].actor == "HUMAN"
    assert facts[("track:1", "mute")].evidence_ref == "host:track-1-mute"
    assert hq.store._conn.execute(
        "SELECT COUNT(*) FROM evidence_claims"
    ).fetchone()[0] == evidence_before

    # A new workspace observation invalidates the whole prior Technical Twin.
    hq.workspaces.reconcile_existing(
        workspace.id,
        song_id=song.id,
        relation="SAME_OR_MOVED",
        runtime=runtime(),
        location_ref="file:///song/project-moved",
    )
    assert hq.shadow.state(workspace.id).status == "STALE"
    try:
        record(
            "INCREMENTAL",
            "HUMAN",
            "host:one-fresh-fader",
            events=(ShadowEventInput("TRACK", "track:1", "volume", "SET", -3.0),),
        )
    except HostShadowError:
        pass
    else:
        raise AssertionError("one incremental observation laundered a stale Host Shadow")

    # A verified FULL refresh is the only way back to CURRENT after rebinding.
    fresh = record(
        "FULL",
        "EXTERNAL",
        "host:full-rescan-after-move",
        events=(ShadowEventInput("TRACK", "track:1", "mute", "SET", True),),
    )
    restored = hq.shadow.require_current(workspace.id)
    assert restored.baseline_batch_id == fresh.id
    assert [(fact.field, fact.value) for fact in restored.facts] == [("mute", True)]
    hq.close()

print(
    "DAW-00D CONSUMER SMOKE: GREEN: verified FULL Host Shadow became current, a human incremental change updated only its bounded fact, technical observation created no Song-evidence claim, workspace movement made the whole shadow stale, one fresh delta could not launder stale state, and only a fresh FULL baseline restored CURRENT"
)
