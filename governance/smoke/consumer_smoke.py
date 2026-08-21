#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "APP-01" or state.get("active_increment") != "APP-01C":
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
from n0te2.platforms import PlatformEnvironment  # noqa: E402
from n0te2.profiles import ApplicationProfiles  # noqa: E402
from n0te2.safe_update import ApplicationUpdateCoordinator  # noqa: E402
from n0te2.support import SupportTarget  # noqa: E402
from n0te2.update import PackageActionReceipt  # noqa: E402


class Probe:
    def status(self, process: ProcessIdentity) -> str:
        return "UNKNOWN"


def process(pid: int, token: str) -> ProcessIdentity:
    return ProcessIdentity.from_start_token(
        PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
        pid=pid,
        start_token=token,
    )


def release(target: SupportTarget, release_id: str, version: str, payload: bytes):
    artifact_id = f"artifact-{release_id}"
    record = ArtifactRecord(
        artifact_id=artifact_id,
        target_fingerprint=target.fingerprint,
        package_kind="TEST_PACKAGE",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    seed = hashlib.sha1(release_id.encode("utf-8")).hexdigest()
    manifest = ReleaseManifest(
        release_id=release_id,
        version=version,
        source_commit_sha=seed,
        build_inputs_sha256=hashlib.sha256((release_id + ":build").encode()).hexdigest(),
        dependency_inventory_sha256=hashlib.sha256((release_id + ":deps").encode()).hexdigest(),
        license_inventory_sha256=hashlib.sha256((release_id + ":licenses").encode()).hexdigest(),
        artifacts=(record,),
    )
    authenticity = ManifestAuthenticityEvidence(
        manifest_fingerprint=manifest.fingerprint,
        status="VERIFIED",
        verifier_id="consumer-smoke-verifier",
        scheme="TEST-ONLY-VERIFIER-RECEIPT",
        evidence_ref=f"smoke:manifest:{release_id}",
    )
    return record, manifest, authenticity


class Driver:
    def __init__(
        self,
        *,
        install_state: str,
        install_release: str | None,
        rollback_release: str,
    ) -> None:
        self.install_state = install_state
        self.install_release = install_release
        self.rollback_release = rollback_release
        self.calls: list[str] = []

    def perform(self, request, artifact_bytes):
        self.calls.append(request.action)
        if request.action == "INSTALL":
            return PackageActionReceipt(
                request_fingerprint=request.fingerprint,
                action="INSTALL",
                state=self.install_state,
                evidence_ref=f"smoke:install:{self.install_state.lower()}",
                resulting_release_id=self.install_release,
            )
        return PackageActionReceipt(
            request_fingerprint=request.fingerprint,
            action="ROLLBACK",
            state="SUCCEEDED",
            evidence_ref="smoke:rollback:succeeded",
            resulting_release_id=self.rollback_release,
        )


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    data_root = root / "data"
    state_root = root / "state"
    proc = process(7001, "app-update-owner")
    probe = Probe()

    profiles = ApplicationProfiles(data_root=data_root, state_root=state_root)
    created = profiles.resolve(
        artist_name="Update Safety Artist",
        process=proc,
        probe=probe,
    )
    assert created.state == "CREATED"
    assert created.selected_profile_id is not None
    profile_id = created.selected_profile_id

    runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert runtime.launch(profile_id=profile_id, process=proc, probe=probe).status == "STARTED"
    song = runtime.headquarters.store.create_song("Update Safety Song")
    version = runtime.headquarters.store.create_version(song.id, label="Protected version")

    target = SupportTarget.from_runtime_labels(os_name="Linux", machine="x86_64")
    payload_v2 = b"n0te2-smoke-release-v2"
    artifact_v2, manifest_v2, auth_v2 = release(target, "release-v2", "2.0.0", payload_v2)
    updater = ApplicationUpdateCoordinator(state_root=state_root)
    plan_v2 = updater.prepare(
        runtime=runtime,
        current_release_id="release-v1",
        manifest=manifest_v2,
        artifact_id=artifact_v2.artifact_id,
        artifact_bytes=payload_v2,
        target=target,
        authenticity=auth_v2,
        process=proc,
        probe=probe,
    )
    assert runtime.state == "STOPPED"

    installed = updater.apply(
        plan=plan_v2,
        data_root=data_root,
        manifest=manifest_v2,
        artifact_bytes=payload_v2,
        target=target,
        authenticity=auth_v2,
        driver=Driver(
            install_state="SUCCEEDED",
            install_release="release-v2",
            rollback_release="release-v1",
        ),
        process=proc,
        probe=probe,
    )
    assert installed.state == "SUCCEEDED"
    assert updater.status(plan_v2.update_id).retry_allowed is False

    resumed_runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert resumed_runtime.launch(
        profile_id=profile_id,
        process=proc,
        probe=probe,
    ).status == "STARTED"
    resumed = resumed_runtime.headquarters.store.active_song()
    assert resumed is not None
    assert resumed.id == song.id
    assert resumed.current_version_id == version.id

    # A second authenticated update reports that package state changed before
    # failure. APP-01C must compensate package first, then restore the exact
    # creative snapshot, and never call the failed package state a success.
    payload_v3 = b"n0te2-smoke-release-v3"
    artifact_v3, manifest_v3, auth_v3 = release(target, "release-v3", "3.0.0", payload_v3)
    plan_v3 = updater.prepare(
        runtime=resumed_runtime,
        current_release_id="release-v2",
        manifest=manifest_v3,
        artifact_id=artifact_v3.artifact_id,
        artifact_bytes=payload_v3,
        target=target,
        authenticity=auth_v3,
        process=proc,
        probe=probe,
    )
    failing_driver = Driver(
        install_state="FAILED_CHANGED",
        install_release=None,
        rollback_release="release-v2",
    )
    rolled_back = updater.apply(
        plan=plan_v3,
        data_root=data_root,
        manifest=manifest_v3,
        artifact_bytes=payload_v3,
        target=target,
        authenticity=auth_v3,
        driver=failing_driver,
        process=proc,
        probe=probe,
    )
    assert rolled_back.state == "ROLLED_BACK"
    assert rolled_back.restored_sha256 == plan_v3.snapshot_sha256
    assert failing_driver.calls == ["INSTALL", "ROLLBACK"]

    final_runtime = ApplicationRuntime(data_root=data_root, state_root=state_root)
    assert final_runtime.launch(profile_id=profile_id, process=proc, probe=probe).status == "STARTED"
    final_song = final_runtime.headquarters.store.active_song()
    assert final_song is not None
    assert final_song.id == song.id
    assert final_song.current_version_id == version.id
    assert final_runtime.quit().status == "STOPPED"

print(
    "APP-01C CONSUMER SMOKE: GREEN: a fresh local Artist profile created a durable Song/version, an authenticated package update stopped the owned runtime, held profile maintenance ownership, installed once, reopened and integrity-checked the same Headquarters, released maintenance ownership and resumed the exact Song; a second authenticated update that failed after package mutation rolled the package back first, restored the exact creative snapshot, resumed the same Song/version, and never converted ambiguous or compensated state into success"
)
