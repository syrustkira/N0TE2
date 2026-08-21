#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "PLATFORM-00" or state.get("active_increment") != "PLATFORM-00D":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.artifacts import (  # noqa: E402
    ArtifactRecord,
    ManifestAuthenticityEvidence,
    ReleaseArtifactVerifier,
    ReleaseManifest,
)
from n0te2.support import SupportTarget  # noqa: E402


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


mac = SupportTarget.from_runtime_labels(os_name="Darwin", machine="arm64")
windows = SupportTarget.from_runtime_labels(os_name="Windows", machine="amd64")
mac_bytes = b"n0te-macos-arm64-release"
windows_bytes = b"n0te-windows-x64-release"

manifest = ReleaseManifest(
    release_id="release-current",
    version="1.2.0",
    source_commit_sha="a" * 40,
    build_inputs_sha256="b" * 64,
    dependency_inventory_sha256="c" * 64,
    license_inventory_sha256="d" * 64,
    artifacts=(
        ArtifactRecord(
            "mac-arm64",
            mac.fingerprint,
            "pkg",
            len(mac_bytes),
            digest(mac_bytes),
        ),
        ArtifactRecord(
            "windows-x64",
            windows.fingerprint,
            "msix",
            len(windows_bytes),
            digest(windows_bytes),
        ),
    ),
)

missing_auth = ReleaseArtifactVerifier.verify(
    manifest=manifest,
    artifact_id="mac-arm64",
    artifact_bytes=mac_bytes,
    expected_target=mac,
    authenticity=None,
)
assert missing_auth.status == "MANIFEST_UNAUTHENTICATED"

auth = ManifestAuthenticityEvidence(
    manifest_fingerprint=manifest.fingerprint,
    status="VERIFIED",
    verifier_id="platform-release-verifier",
    scheme="platform-authenticity",
    evidence_ref="verify:release-current",
)
ready = ReleaseArtifactVerifier.verify(
    manifest=manifest,
    artifact_id="mac-arm64",
    artifact_bytes=mac_bytes,
    expected_target=mac,
    authenticity=auth,
)
assert ready.status == "READY"

wrong_target = ReleaseArtifactVerifier.verify(
    manifest=manifest,
    artifact_id="mac-arm64",
    artifact_bytes=mac_bytes,
    expected_target=windows,
    authenticity=auth,
)
assert wrong_target.status == "TARGET_MISMATCH"

tampered = bytearray(mac_bytes)
tampered[-1] ^= 1
tampered_result = ReleaseArtifactVerifier.verify(
    manifest=manifest,
    artifact_id="mac-arm64",
    artifact_bytes=tampered,
    expected_target=mac,
    authenticity=auth,
)
assert tampered_result.status == "HASH_MISMATCH"

other_manifest = ReleaseManifest(
    release_id="release-other",
    version="1.2.1",
    source_commit_sha="e" * 40,
    build_inputs_sha256="b" * 64,
    dependency_inventory_sha256="c" * 64,
    license_inventory_sha256="d" * 64,
    artifacts=manifest.artifacts,
)
wrong_manifest_auth = ManifestAuthenticityEvidence(
    manifest_fingerprint=other_manifest.fingerprint,
    status="VERIFIED",
    verifier_id="platform-release-verifier",
    scheme="platform-authenticity",
    evidence_ref="verify:release-other",
)
mismatch = ReleaseArtifactVerifier.verify(
    manifest=manifest,
    artifact_id="mac-arm64",
    artifact_bytes=mac_bytes,
    expected_target=mac,
    authenticity=wrong_manifest_auth,
)
assert mismatch.status == "MANIFEST_MISMATCH"

# Artifact trust deliberately does not enforce release ordering. An explicitly
# selected older/rollback release can still pass the same authenticity/byte gates.
rollback = ReleaseManifest(
    release_id="release-rollback",
    version="1.1.0",
    source_commit_sha="f" * 40,
    build_inputs_sha256="1" * 64,
    dependency_inventory_sha256="2" * 64,
    license_inventory_sha256="3" * 64,
    artifacts=(
        ArtifactRecord(
            "mac-arm64",
            mac.fingerprint,
            "pkg",
            len(mac_bytes),
            digest(mac_bytes),
        ),
    ),
)
rollback_auth = ManifestAuthenticityEvidence(
    manifest_fingerprint=rollback.fingerprint,
    status="VERIFIED",
    verifier_id="platform-release-verifier",
    scheme="platform-authenticity",
    evidence_ref="verify:release-rollback",
)
rollback_ready = ReleaseArtifactVerifier.verify(
    manifest=rollback,
    artifact_id="mac-arm64",
    artifact_bytes=mac_bytes,
    expected_target=mac,
    authenticity=rollback_auth,
)
assert rollback_ready.status == "READY"

public = {
    name
    for name in dir(ReleaseArtifactVerifier)
    if not name.startswith("_") and callable(getattr(ReleaseArtifactVerifier, name))
}
assert public == {"verify"}
assert not ({"install", "update", "rollback", "sign", "download", "execute"} & public)

print(
    "PLATFORM-00D CONSUMER SMOKE: GREEN: unauthenticated manifest bytes failed closed, exact authenticated manifest+target+bytes became READY, wrong target and byte tampering were rejected distinctly, authenticity for another manifest revision could not authorize this release, an explicitly selected rollback artifact could still pass trust verification, and the verifier exposed no signing/install/update/download/execute verb"
)
