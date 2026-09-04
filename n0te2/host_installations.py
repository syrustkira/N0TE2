from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .hosts import CORE_HOST_FAMILIES
from .platforms import PlatformEnvironment

INSTALLATION_SCAN_VERSION = "HOST_INSTALLATION_STANDARD_PATH_V1"
STANDARD_SCAN = "STANDARD_SCAN"
NO_STANDARD_SCAN = "NO_STANDARD_SCAN"
INSTALLATION_SOURCE_CLASS = "STANDARD_INSTALL_LOCATION"
_MAX_MATCHES_PER_PATTERN = 32

_DISPLAY_NAMES = {
    "ABLETON_LIVE": "Ableton Live",
    "FL_STUDIO": "FL Studio",
    "LOGIC_PRO": "Logic Pro",
    "PRO_TOOLS": "Pro Tools",
    "STUDIO_ONE": "Studio One",
    "REAPER": "REAPER",
}


class HostInstallationError(RuntimeError):
    """A local host-installation scan could not remain inside its truth boundary."""


@dataclass(frozen=True)
class HostInstallationObservation:
    """Positive local installation evidence, deliberately not host support truth."""

    family: str
    display_name: str
    os_family: str
    source_class: str
    entry_kind: str
    location_fingerprint: str
    scan_version: str = INSTALLATION_SCAN_VERSION

    def __post_init__(self) -> None:
        if self.family not in CORE_HOST_FAMILIES:
            raise HostInstallationError(f"unsupported peer host family: {self.family}")
        if self.display_name != _DISPLAY_NAMES[self.family]:
            raise HostInstallationError("display_name does not match the peer host family")
        if self.os_family not in {"MACOS", "WINDOWS"}:
            raise HostInstallationError("positive standard-path observations require macOS or Windows")
        if self.source_class != INSTALLATION_SOURCE_CLASS:
            raise HostInstallationError("installation source class is invalid")
        if self.entry_kind not in {"APPLICATION_BUNDLE", "EXECUTABLE"}:
            raise HostInstallationError("installation entry kind is invalid")
        digest = str(self.location_fingerprint).strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise HostInstallationError("location_fingerprint must be a SHA-256 digest")
        object.__setattr__(self, "location_fingerprint", digest)


@dataclass(frozen=True)
class HostInstallationInventory:
    """Ephemeral bounded scan result. Unknown is preserved as unknown."""

    os_family: str
    scan_state: str
    observations: tuple[HostInstallationObservation, ...]
    unknown_families: tuple[str, ...]
    scan_version: str = INSTALLATION_SCAN_VERSION

    def __post_init__(self) -> None:
        if self.scan_state not in {STANDARD_SCAN, NO_STANDARD_SCAN}:
            raise HostInstallationError("invalid host installation scan state")
        seen = tuple(observation.family for observation in self.observations)
        if len(seen) != len(set(seen)):
            raise HostInstallationError("host installation inventory must contain at most one observation per family")
        unknown = tuple(self.unknown_families)
        expected_unknown = tuple(family for family in CORE_HOST_FAMILIES if family not in seen)
        if unknown != expected_unknown:
            raise HostInstallationError("unknown host families do not match positive observations")
        if any(observation.os_family != self.os_family for observation in self.observations):
            raise HostInstallationError("installation observations cross platform boundaries")
        if self.scan_state == NO_STANDARD_SCAN and self.observations:
            raise HostInstallationError("NO_STANDARD_SCAN cannot contain positive observations")

    @property
    def absence_is_unknown(self) -> bool:
        return True

    def observed(self, family: str) -> bool:
        return any(item.family == family for item in self.observations)


@dataclass(frozen=True)
class _ProbeSpec:
    family: str
    root_key: str
    pattern: str
    entry_kind: str


_MAC_SPECS = (
    _ProbeSpec("ABLETON_LIVE", "APPLICATIONS", "Ableton Live*.app", "APPLICATION_BUNDLE"),
    _ProbeSpec("ABLETON_LIVE", "USER_APPLICATIONS", "Ableton Live*.app", "APPLICATION_BUNDLE"),
    _ProbeSpec("FL_STUDIO", "APPLICATIONS", "FL Studio.app", "APPLICATION_BUNDLE"),
    _ProbeSpec("FL_STUDIO", "USER_APPLICATIONS", "FL Studio.app", "APPLICATION_BUNDLE"),
    _ProbeSpec("LOGIC_PRO", "APPLICATIONS", "Logic Pro.app", "APPLICATION_BUNDLE"),
    _ProbeSpec("LOGIC_PRO", "USER_APPLICATIONS", "Logic Pro.app", "APPLICATION_BUNDLE"),
    _ProbeSpec("PRO_TOOLS", "APPLICATIONS", "Pro Tools.app", "APPLICATION_BUNDLE"),
    _ProbeSpec("PRO_TOOLS", "USER_APPLICATIONS", "Pro Tools.app", "APPLICATION_BUNDLE"),
    _ProbeSpec("STUDIO_ONE", "APPLICATIONS", "Studio One*.app", "APPLICATION_BUNDLE"),
    _ProbeSpec("STUDIO_ONE", "USER_APPLICATIONS", "Studio One*.app", "APPLICATION_BUNDLE"),
    _ProbeSpec("REAPER", "APPLICATIONS", "REAPER.app", "APPLICATION_BUNDLE"),
    _ProbeSpec("REAPER", "USER_APPLICATIONS", "REAPER.app", "APPLICATION_BUNDLE"),
)

_WINDOWS_SPECS = (
    _ProbeSpec(
        "ABLETON_LIVE",
        "PROGRAMDATA",
        "Ableton/Live */Program/Ableton Live *.exe",
        "EXECUTABLE",
    ),
    _ProbeSpec("FL_STUDIO", "PROGRAMFILES", "Image-Line/FL Studio */FL64.exe", "EXECUTABLE"),
    _ProbeSpec("FL_STUDIO", "PROGRAMFILES_X86", "Image-Line/FL Studio */FL.exe", "EXECUTABLE"),
    _ProbeSpec("PRO_TOOLS", "PROGRAMFILES", "Avid/Pro Tools/ProTools.exe", "EXECUTABLE"),
    _ProbeSpec("PRO_TOOLS", "PROGRAMFILES_X86", "Avid/Pro Tools/ProTools.exe", "EXECUTABLE"),
    _ProbeSpec("STUDIO_ONE", "PROGRAMFILES", "PreSonus/Studio One */Studio One.exe", "EXECUTABLE"),
    _ProbeSpec("STUDIO_ONE", "PROGRAMFILES_X86", "PreSonus/Studio One */Studio One.exe", "EXECUTABLE"),
    _ProbeSpec("REAPER", "PROGRAMFILES", "REAPER (x64)/reaper.exe", "EXECUTABLE"),
    _ProbeSpec("REAPER", "PROGRAMFILES", "REAPER/reaper.exe", "EXECUTABLE"),
    _ProbeSpec("REAPER", "PROGRAMFILES_X86", "REAPER/reaper.exe", "EXECUTABLE"),
)


def _location_fingerprint(path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve())).encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(normalized).hexdigest()


def _safe_root(root: Path) -> Path | None:
    if not root.is_absolute():
        return None
    try:
        info = os.lstat(root)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return None
    try:
        return root.resolve(strict=True)
    except OSError:
        return None


def _safe_matches(root: Path, pattern: str, entry_kind: str) -> tuple[Path, ...]:
    resolved_root = _safe_root(root)
    if resolved_root is None:
        return ()
    matches: list[Path] = []
    try:
        candidates: Iterable[Path] = root.glob(pattern)
        for candidate in candidates:
            if len(matches) >= _MAX_MATCHES_PER_PATTERN:
                break
            try:
                info = os.lstat(candidate)
                if stat.S_ISLNK(info.st_mode):
                    continue
                resolved = candidate.resolve(strict=True)
                if not resolved.is_relative_to(resolved_root):
                    continue
                resolved_info = os.lstat(resolved)
            except (FileNotFoundError, NotADirectoryError, OSError, RuntimeError):
                continue
            if entry_kind == "APPLICATION_BUNDLE":
                if not stat.S_ISDIR(resolved_info.st_mode):
                    continue
            elif entry_kind == "EXECUTABLE":
                if not stat.S_ISREG(resolved_info.st_mode):
                    continue
            else:
                raise HostInstallationError(f"unsupported entry kind: {entry_kind}")
            matches.append(resolved)
    except OSError:
        return ()
    return tuple(matches)


def scan_host_installations(
    platform: PlatformEnvironment,
    *,
    roots: Mapping[str, Path],
) -> HostInstallationInventory:
    """Scan bounded standard locations without launching, persisting or inferring support.

    A positive result means only that a safe regular installation entry exists under
    one of the supplied standard roots. A missing match is intentionally UNKNOWN.
    """

    if not isinstance(platform, PlatformEnvironment):
        raise TypeError("platform must be PlatformEnvironment")
    if platform.os_family == "MACOS":
        specs = _MAC_SPECS
        scan_state = STANDARD_SCAN
    elif platform.os_family == "WINDOWS":
        specs = _WINDOWS_SPECS
        scan_state = STANDARD_SCAN
    else:
        return HostInstallationInventory(
            os_family=platform.os_family,
            scan_state=NO_STANDARD_SCAN,
            observations=(),
            unknown_families=CORE_HOST_FAMILIES,
        )

    normalized_roots: dict[str, Path] = {}
    for key, value in roots.items():
        if not isinstance(value, Path):
            raise TypeError("host installation roots must contain pathlib.Path values")
        normalized_roots[str(key).upper()] = value

    observations: list[HostInstallationObservation] = []
    observed_families: set[str] = set()
    for spec in specs:
        if spec.family in observed_families:
            continue
        root = normalized_roots.get(spec.root_key)
        if root is None:
            continue
        matches = _safe_matches(root, spec.pattern, spec.entry_kind)
        if not matches:
            continue
        observations.append(
            HostInstallationObservation(
                family=spec.family,
                display_name=_DISPLAY_NAMES[spec.family],
                os_family=platform.os_family,
                source_class=INSTALLATION_SOURCE_CLASS,
                entry_kind=spec.entry_kind,
                location_fingerprint=_location_fingerprint(matches[0]),
            )
        )
        observed_families.add(spec.family)

    observations.sort(key=lambda item: CORE_HOST_FAMILIES.index(item.family))
    unknown = tuple(family for family in CORE_HOST_FAMILIES if family not in observed_families)
    return HostInstallationInventory(
        os_family=platform.os_family,
        scan_state=scan_state,
        observations=tuple(observations),
        unknown_families=unknown,
    )


def runtime_host_installation_inventory(
    platform: PlatformEnvironment,
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> HostInstallationInventory:
    """Build only standard roots for the current runtime platform and scan them."""

    if not isinstance(platform, PlatformEnvironment):
        raise TypeError("platform must be PlatformEnvironment")
    env = {str(key).upper(): str(value) for key, value in (os.environ if environment is None else environment).items()}
    user_home = Path.home() if home is None else Path(home)

    if platform.os_family == "MACOS":
        roots = {
            "APPLICATIONS": Path("/Applications"),
            "USER_APPLICATIONS": user_home / "Applications",
        }
    elif platform.os_family == "WINDOWS":
        roots: dict[str, Path] = {}
        for key, fallback in (
            ("PROGRAMDATA", r"C:\ProgramData"),
            ("PROGRAMFILES", r"C:\Program Files"),
            ("PROGRAMFILES_X86", r"C:\Program Files (x86)"),
        ):
            value = env.get("PROGRAMFILES(X86)") if key == "PROGRAMFILES_X86" else env.get(key)
            roots[key] = Path(value or fallback)
    else:
        roots = {}
    return scan_host_installations(platform, roots=roots)
