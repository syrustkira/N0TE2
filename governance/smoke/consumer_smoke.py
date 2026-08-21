#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sqlite3
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

from n0te2.app_runtime import ApplicationRuntime  # noqa: E402
from n0te2.artifacts import (  # noqa: E402
    ArtifactRecord,
    ManifestAuthenticityEvidence,
    ReleaseManifest,
)
from n0te2.instance import ProcessIdentity  # noqa: E402
from n0te2.migration import MigrationStep  # noqa: E402
from n0te2.platforms import PlatformEnvironment  # noqa: E402
from n0te2.safe_update import ApplicationUpdateCoordinator  # noqa: E402
from n0te2.support import SupportTarget  # noqa: E402
from n0te2.update import PackageActionReceipt  # noqa: E402


class Probe:
    def status(self, process: ProcessIdentity) -> str:
        return "DEAD"


class SuccessfulPackageDriver:
    def perform(self, request, artifact_bytes):
        if request.action == "INSTALL":
            return PackageActionReceipt(
                request_fingerprint=request.fingerprint,
                action="INSTALL",
                state="SUCCEEDED",
                evidence_ref="smoke:package-installed",
                resulting_release_id=request.target_release_id,
            )
        return PackageActionReceipt(
            request_fingerprint=request.fingerprint,
            action="ROLLBACK",
            state="SUCCEEDED",
            evidence_ref="smoke:package-rolled-back",
            resulting_release_id=request.current_release_id,
        )


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp).resolve()
    data_root = (root / "data").resolve()
    state_root = (root / "state").resolve()
    platform = PlatformEnvironment.from_runtime_labels("Linux", "x86_64")
    process = ProcessIdentity.from_start_token(
        platform,
        pid=99001,
        start_token="app-01e-update-consumer-smoke",
    )
    probe = Probe()

    from n0te2.memory import HeadquartersMemory  # noqa: E402

    seed = HeadquartersMemory.create(data_root, "Schema Update Artist")
    profile_id = seed.store.profile_id
    artist_id = seed.store.primary_artist_id
    seed.close()

    runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert runtime.launch(profile_id=profile_id, process=process, probe=probe).status == "STARTED"
    song = runtime.headquarters.store.create_song("Migration-Safe Song")
    asset = runtime.headquarters.store.attach_asset(
        song.id,
        name="song.wav",
        sha256="a" * 64,
        source_uri="file:///song.wav",
    )
    version = runtime.headquarters.store.create_version(
        song.id,
        label="Pre-Update Version",
        asset_ids=(asset.id,),
    )

    package_bytes = b"n0te2-app-01e-release"
    target = SupportTarget.from_runtime_labels(os_name="Linux", machine="x86_64")
    artifact = ArtifactRecord(
        artifact_id="linux-x86_64-package",
        target_fingerprint=target.fingerprint,
        package_kind="TEST_PACKAGE",
        size_bytes=len(package_bytes),
        sha256=hashlib.sha256(package_bytes).hexdigest(),
    )
    manifest = ReleaseManifest(
        release_id="release-app-01e",
        version="2.0.0",
        source_commit_sha="a" * 40,
        build_inputs_sha256="b" * 64,
        dependency_inventory_sha256="c" * 64,
        license_inventory_sha256="d" * 64,
        artifacts=(artifact,),
    )
    authenticity = ManifestAuthenticityEvidence(
        manifest_fingerprint=manifest.fingerprint,
        status="VERIFIED",
        verifier_id="smoke-verifier",
        scheme="SMOKE-ONLY-VERIFIER-RECEIPT",
        evidence_ref="smoke:manifest-authenticity",
    )
    step = MigrationStep(
        1,
        2,
        "release-app-01e semantic schema",
        ("CREATE TABLE app_v2_marker(value TEXT NOT NULL DEFAULT 'ready')",),
    )

    coordinator = ApplicationUpdateCoordinator(state_root=state_root)
    plan = coordinator.prepare(
        runtime=runtime,
        current_release_id="release-v1",
        manifest=manifest,
        artifact_id=artifact.artifact_id,
        artifact_bytes=package_bytes,
        target=target,
        authenticity=authenticity,
        process=process,
        probe=probe,
        schema_target_version=2,
        schema_steps=(step,),
    )
    assert runtime.state == "STOPPED"

    result = coordinator.apply(
        plan=plan,
        data_root=data_root,
        manifest=manifest,
        artifact_bytes=package_bytes,
        target=target,
        authenticity=authenticity,
        driver=SuccessfulPackageDriver(),
        process=process,
        probe=probe,
    )
    assert result.state == "SUCCEEDED"

    db = data_root / "profiles" / profile_id / "lineage.sqlite3"
    conn = sqlite3.connect(db)
    try:
        assert int(
            conn.execute(
                "SELECT value FROM metadata WHERE key='application_semantic_schema_version'"
            ).fetchone()[0]
        ) == 2
        history = conn.execute(
            "SELECT migration_id,from_version,to_version,step_fingerprint,description "
            "FROM application_schema_migrations ORDER BY sequence"
        ).fetchall()
        assert len(history) == 1
        assert history[0][1:] == (1, 2, step.fingerprint, step.description)
    finally:
        conn.close()

    resumed_runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert resumed_runtime.launch(
        profile_id=profile_id,
        process=process,
        probe=probe,
    ).status == "STARTED"
    reopened = resumed_runtime.headquarters
    assert reopened.store.primary_artist_id == artist_id
    resumed = reopened.store.active_song()
    assert resumed is not None and resumed.id == song.id
    assert resumed.current_version_id == version.id
    reopened_version = reopened.store.get_version(version.id)
    assert reopened_version is not None and reopened_version.song_id == song.id
    reopened_asset = reopened.store.get_asset(asset.id)
    assert reopened_asset is not None and reopened_asset.song_id == song.id
    assert resumed_runtime.quit().status == "STOPPED"

print(
    "APP-01E CONSUMER SMOKE: GREEN: an authenticated target package was prepared from a running Artist profile, the runtime stopped, package installation executed under update maintenance ownership, the bound semantic schema migrated 1→2 before Headquarters validation, and the exact Artist/Song/version/asset identities reopened normally afterward"
)
