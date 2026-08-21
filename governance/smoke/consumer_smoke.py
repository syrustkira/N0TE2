#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
state = json.loads((repo / "governance/current_state.json").read_text())
if state.get("active_node") != "PLATFORM-00" or state.get("active_increment") != "PLATFORM-00A":
    raise SystemExit(
        f"STAGE SMOKE: RED: unsupported active stage {state.get('active_node')}/{state.get('active_increment')}"
    )

from n0te2.platforms import PlatformEnvironment, PlatformError, resolve_application_roots  # noqa: E402

profile_id = "profile_consumer_smoke"
mac = PlatformEnvironment.from_runtime_labels("Darwin", "arm64")
windows = PlatformEnvironment.from_runtime_labels("Windows", "AMD64")
linux = PlatformEnvironment.from_runtime_labels("Linux", "aarch64")
assert mac.target_tier == windows.target_tier == linux.target_tier == "CORE_TARGET"

mac_roots = resolve_application_roots(mac, home="/Users/artist")
windows_roots = resolve_application_roots(
    windows,
    home=r"C:\Users\Artist",
    environment={"APPDATA": r"D:\Roaming", "LOCALAPPDATA": r"E:\Local"},
)
linux_roots = resolve_application_roots(
    linux,
    home="/home/artist",
    environment={
        "XDG_DATA_HOME": "/var/n0te-data",
        "XDG_CONFIG_HOME": "/var/n0te-config",
        "XDG_STATE_HOME": "/var/n0te-state",
        "XDG_CACHE_HOME": "/var/n0te-cache",
    },
)

assert str(mac_roots.data_root) == "/Users/artist/Library/Application Support/N0TE"
assert str(windows_roots.config_root) == r"D:\Roaming\N0TE"
assert str(windows_roots.data_root) == r"E:\Local\N0TE\Data"
assert str(linux_roots.data_root) == "/var/n0te-data/N0TE"
assert str(linux_roots.log_root) == "/var/n0te-state/N0TE/logs"

# A platform/root move changes storage location, never the opaque profile identity.
assert mac_roots.profile_data_root(profile_id).name == profile_id
assert windows_roots.profile_data_root(profile_id).name == profile_id
assert linux_roots.profile_data_root(profile_id).name == profile_id

for roots in (mac_roots, windows_roots, linux_roots):
    rendered = "|".join(
        str(value)
        for value in (
            roots.data_root,
            roots.config_root,
            roots.state_root,
            roots.cache_root,
            roots.log_root,
        )
    )
    assert ".n0te-ableton-ai" not in rendered.lower()
    assert "N0TE" in rendered

# Pure resolution must not create a consumer's application tree as a side effect.
with tempfile.TemporaryDirectory() as temp:
    not_created_home = Path(temp) / "home-does-not-exist"
    resolved = resolve_application_roots(linux, home=str(not_created_home))
    assert not not_created_home.exists()
    assert not Path(str(resolved.data_root)).exists()

# Unsupported systems and relative roots are explicit failures, not guessed portability.
unsupported = PlatformEnvironment.from_runtime_labels("FreeBSD", "amd64")
assert unsupported.target_tier == "UNSUPPORTED_PLATFORM"
try:
    resolve_application_roots(unsupported, home="/home/artist")
except PlatformError:
    pass
else:
    raise AssertionError("unsupported platform was assigned application roots")

try:
    resolve_application_roots(linux, home="relative-home")
except PlatformError:
    pass
else:
    raise AssertionError("relative home was accepted")

print(
    "PLATFORM-00A CONSUMER SMOKE: GREEN: macOS, Windows and Linux resolved deterministic N0TE application roots from one platform-neutral contract, preserved the same opaque profile identity across root changes, created no filesystem state, and never revived the legacy Ableton-branded path"
)
