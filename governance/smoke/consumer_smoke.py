#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "DAW-00" or state.get("active_increment") != "DAW-00E":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.hosts import HostRuntimeIdentity  # noqa: E402
from n0te2.memory import HeadquartersMemory  # noqa: E402
from n0te2.reconcile import ReconciliationError, ReconciliationTarget  # noqa: E402
from n0te2.shadow import ShadowEventInput  # noqa: E402


def runtime():
    return HostRuntimeIdentity.from_runtime_labels(
        host_family="ABLETON_LIVE",
        version="12.1",
        edition="Suite",
        os_name="Darwin",
        machine="arm64",
    )


with tempfile.TemporaryDirectory() as temp:
    hq = HeadquartersMemory.create(Path(temp), "Artist")
    song = hq.store.create_song("Reconciliation Song")
    workspace = hq.workspaces.create(
        song.id,
        runtime=runtime(),
        location_ref="file:///song/project",
    )
    observation = hq.workspaces.state(workspace.id).current_observation
    full = hq.shadow.record_batch(
        workspace.id,
        workspace_observation_id=observation.id,
        host_runtime_fingerprint=observation.host_runtime_fingerprint,
        coverage="FULL",
        actor="EXTERNAL",
        evidence_ref="host:verified-full-scan",
        verified=True,
        events=(
            ShadowEventInput(
                "TRACK",
                "track:1",
                "mute",
                "SET",
                True,
                "host:track-1-mute",
            ),
        ),
    )
    claim = hq.evidence.record_claim(
        scope_kind="SONG",
        scope_id=song.id,
        key="workspace.track.track:1.mute",
        value=False,
        source_kind="USER_DECLARED",
        source_ref="song:technical-intent",
        confidence=1.0,
        twin_domain="TECHNICAL",
    )
    target = ReconciliationTarget(
        "workspace.track.track:1.mute",
        "TRACK",
        "track:1",
        "mute",
    )

    comparison = hq.reconciliation.compare(
        song_id=song.id,
        workspace_id=workspace.id,
        target=target,
    )
    assert comparison.status == "CONFLICT"
    assert comparison.canonical_claim_ids == (claim.id,)
    assert comparison.canonical_values == (False,)
    assert comparison.host_fact is not None
    assert comparison.host_fact.value is True
    assert comparison.host_baseline_batch_id == full.id
    assert comparison.host_latest_batch_id == full.id

    case = hq.reconciliation.open_case(
        song_id=song.id,
        workspace_id=workspace.id,
        target=target,
    )
    evidence_before = hq.store._conn.execute(
        "SELECT COUNT(*) FROM evidence_claims"
    ).fetchone()[0]
    shadow_before = hq.store._conn.execute(
        "SELECT COUNT(*) FROM host_shadow_batches"
    ).fetchone()[0]
    decision = hq.reconciliation.record_decision(
        case.id,
        choice="KEEP_WORKSPACE_SPECIFIC",
        evidence_ref="user:keep-workspace-specific",
        rationale="Keep the current DAW variation without changing canonical Song intent.",
    )
    assert decision.choice == "KEEP_WORKSPACE_SPECIFIC"
    assert hq.reconciliation.state(case.id).status == "DECIDED"
    assert hq.store._conn.execute(
        "SELECT COUNT(*) FROM evidence_claims"
    ).fetchone()[0] == evidence_before
    assert hq.store._conn.execute(
        "SELECT COUNT(*) FROM host_shadow_batches"
    ).fetchone()[0] == shadow_before
    assert case.id in {
        item.case.id
        for item in hq.reconciliation.unresolved_for_workspace(workspace.id)
    }

    # New verified host evidence makes the old decision receipt stale, even if
    # the latest host value now happens to match canonical memory.
    observation = hq.workspaces.state(workspace.id).current_observation
    hq.shadow.record_batch(
        workspace.id,
        workspace_observation_id=observation.id,
        host_runtime_fingerprint=observation.host_runtime_fingerprint,
        coverage="INCREMENTAL",
        actor="HUMAN",
        evidence_ref="host:user-unmuted-track",
        verified=True,
        events=(
            ShadowEventInput(
                "TRACK",
                "track:1",
                "mute",
                "SET",
                False,
                "host:track-1-unmute",
            ),
        ),
    )
    assert hq.reconciliation.state(case.id).status == "STALE"
    try:
        hq.reconciliation.record_decision(
            case.id,
            choice="DO_NOTHING",
            evidence_ref="user:stale-choice",
        )
    except ReconciliationError:
        pass
    else:
        raise AssertionError("stale reconciliation receipt accepted a new decision")
    hq.close()

print(
    "DAW-00E CONSUMER SMOKE: GREEN: canonical Song technical memory and verified Host Shadow disagreement produced an explicit CONFLICT case with exact provenance, KEEP_WORKSPACE_SPECIFIC recorded a decision without changing either side, the decided case remained visible as pending, new host evidence made the receipt STALE, and stale evidence could not accept another decision"
)
