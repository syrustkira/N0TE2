#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "APP-01" or state.get("active_increment") != "APP-01E":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.instance import ProcessIdentity  # noqa: E402
from n0te2.memory import HeadquartersMemory  # noqa: E402
from n0te2.migration import ApplicationSchemaMigrator, MigrationStep  # noqa: E402
from n0te2.platforms import PlatformEnvironment  # noqa: E402


class DeadProbe:
    def status(self, process: ProcessIdentity) -> str:
        return "DEAD"


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp).resolve()
    data_root = (root / "data").resolve()
    state_root = (root / "state").resolve()

    headquarters = HeadquartersMemory.create(data_root, "Schema Migration Artist")
    profile_id = headquarters.store.profile_id
    artist_id = headquarters.store.primary_artist_id
    song = headquarters.store.create_song("Migration-Safe Song")
    asset = headquarters.store.attach_asset(
        song.id,
        name="song.wav",
        sha256="a" * 64,
        source_uri="file:///song.wav",
    )
    version = headquarters.store.create_version(
        song.id,
        label="Pre-Migration Version",
        asset_ids=(asset.id,),
    )
    headquarters.close()

    migrator = ApplicationSchemaMigrator(data_root, state_root)
    step = MigrationStep(
        1,
        2,
        "add bounded application schema v2 marker",
        ("CREATE TABLE app_v2_marker(value TEXT NOT NULL DEFAULT 'ready')",),
    )
    plan = migrator.prepare(profile_id=profile_id, target_version=2, steps=(step,))
    platform = PlatformEnvironment.from_runtime_labels("Linux", "x86_64")
    maintenance = ProcessIdentity.from_start_token(
        platform,
        pid=99001,
        start_token="app-01e-consumer-smoke",
    )
    result = migrator.migrate(plan, maintenance_process=maintenance, probe=DeadProbe())
    assert result.state == "SUCCEEDED"
    assert result.installed_version == 2

    history = migrator.history(profile_id)
    assert len(history) == 1
    assert history[0].migration_id == plan.migration_id
    assert history[0].from_version == 1
    assert history[0].to_version == 2
    assert history[0].step_fingerprint == step.fingerprint

    reopened = HeadquartersMemory.open(data_root, profile_id)
    assert reopened.store.primary_artist_id == artist_id
    resumed = reopened.store.active_song()
    assert resumed is not None and resumed.id == song.id
    assert resumed.current_version_id == version.id
    reopened_version = reopened.store.get_version(version.id)
    assert reopened_version is not None and reopened_version.song_id == song.id
    reopened_asset = reopened.store.get_asset(asset.id)
    assert reopened_asset is not None and reopened_asset.song_id == song.id
    reopened.close()

print(
    "APP-01E CONSUMER SMOKE: GREEN: a stopped canonical Artist profile migrated through one explicit semantic-schema edge using a staged candidate and maintenance lease; the migration was inspectable, and the exact Artist/Song/version/asset identities reopened normally at the target schema"
)
