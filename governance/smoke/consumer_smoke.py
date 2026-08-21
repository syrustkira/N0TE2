#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path, PurePosixPath

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "APP-01" or state.get("active_increment") != "APP-01D":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.memory import HeadquartersMemory  # noqa: E402
from n0te2.platforms import PlatformRoots  # noqa: E402
from n0te2.uninstall import (  # noqa: E402
    ApplicationRemovalPlanner,
    ResolvedPathEvidence,
)


def verified(path: PurePosixPath, ref: str) -> ResolvedPathEvidence:
    physical = Path(str(path))
    physical.mkdir(parents=True, exist_ok=True)
    assert not physical.is_symlink()
    assert physical.resolve() == physical
    return ResolvedPathEvidence(path, path, False, ref)


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp).resolve()
    data_root = root / "data"
    state_root = root / "state"
    cache_root = root / "cache"
    log_root = state_root / "logs"
    config_root = root / "config"
    app_path = root / "package" / "n0te"
    app_path.parent.mkdir(parents=True)
    app_path.write_text("test package placeholder")

    headquarters = HeadquartersMemory.create(data_root, "Uninstall Retention Artist")
    profile_id = headquarters.store.profile_id
    song = headquarters.store.create_song("Retained Song")
    version = headquarters.store.create_version(song.id, label="Retained Version")
    snapshot = headquarters.recovery.create_snapshot()
    headquarters.close()

    roots = PlatformRoots(
        os_family="LINUX",
        data_root=PurePosixPath(str(data_root)),
        config_root=PurePosixPath(str(config_root)),
        state_root=PurePosixPath(str(state_root)),
        cache_root=PurePosixPath(str(cache_root)),
        log_root=PurePosixPath(str(log_root)),
    )
    plan = ApplicationRemovalPlanner.plan(
        roots=roots,
        profile_id=profile_id,
        runtime_state="STOPPED",
        path_evidence=(
            verified(roots.config_root, "smoke:path:config"),
            verified(roots.state_root, "smoke:path:state"),
            verified(roots.cache_root, "smoke:path:cache"),
            verified(PurePosixPath(str(app_path)), "smoke:path:package"),
        ),
        platform_managed_paths=(PurePosixPath(str(app_path)),),
    )

    retained = {str(entry.path) for entry in plan.retained}
    assert str(roots.data_root) in retained
    assert str(roots.profile_data_root(profile_id)) in retained
    assert str(roots.profile_data_root(profile_id) / "recovery") in retained
    assert str(snapshot.path).startswith(str(roots.profile_data_root(profile_id) / "recovery"))
    assert roots.data_root not in {entry.path for entry in plan.removal_candidates}
    assert roots.profile_data_root(profile_id) not in {entry.path for entry in plan.removal_candidates}
    assert roots.log_root not in {entry.path for entry in plan.removal_candidates}
    assert {entry.path for entry in plan.removal_candidates} == {
        roots.config_root,
        roots.state_root,
        roots.cache_root,
    }
    package = next(entry for entry in plan.entries if entry.path == PurePosixPath(str(app_path)))
    assert package.classification == "PLATFORM_MANAGED"
    assert package.eligible_for_removal is False
    assert all(item.status == "HELD_NOT_ACTIVE" for item in plan.held_services)
    assert all(item.promotion_required for item in plan.held_services)

    reopened = HeadquartersMemory.open(data_root, profile_id)
    resumed = reopened.store.active_song()
    assert resumed is not None and resumed.id == song.id
    assert resumed.current_version_id == version.id
    reopened.close()

print(
    "APP-01D CONSUMER SMOKE: GREEN: a stopped local N0TE profile produced a non-destructive uninstall plan in which package/runtime mechanics were separated from retained Artist/Song/profile/recovery data, nested runtime roots did not double-remove, the real Song/version remained reopenable, and accounts/cloud/billing/telemetry/crash-upload/DRM stayed explicitly held-not-active"
)
