from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from n0te2.consumer_shell import ConsumerShell
from n0te2.host_installations import (
    INSTALLATION_SOURCE_CLASS,
    STANDARD_SCAN,
    HostInstallationInventory,
    HostInstallationObservation,
)
from n0te2.host_installations_shell import install_host_installation_inventory
from n0te2.instance import ProcessIdentity
from n0te2.memory import HeadquartersMemory
from n0te2.platforms import PlatformEnvironment


class Probe:
    def status(self, process: ProcessIdentity) -> str:
        return "UNKNOWN"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def process() -> ProcessIdentity:
    return ProcessIdentity.from_start_token(
        PlatformEnvironment.from_runtime_labels("Darwin", "arm64"),
        pid=9701,
        start_token="host-installation-settings",
        launch_marker="host-installation-settings-launch",
    )


def request(shell: ConsumerShell, path: str) -> tuple[int, str]:
    req = Request(shell.address.origin + path, method="GET")
    try:
        with build_opener(NoRedirect()).open(req, timeout=2.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


@contextmanager
def attached_inventory_surface():
    already_installed = bool(
        getattr(ConsumerShell, "_host_installation_inventory_installed", False)
    )
    original_content = ConsumerShell._state_content
    install_host_installation_inventory()
    try:
        yield
    finally:
        if not already_installed:
            ConsumerShell._state_content = original_content
            ConsumerShell._host_installation_inventory_installed = False


def observation(family: str, display: str, digest_char: str) -> HostInstallationObservation:
    return HostInstallationObservation(
        family=family,
        display_name=display,
        os_family="MACOS",
        source_class=INSTALLATION_SOURCE_CLASS,
        entry_kind="APPLICATION_BUNDLE",
        location_fingerprint=digest_char * 64,
    )


def inventory(*items: HostInstallationObservation) -> HostInstallationInventory:
    seen = {item.family for item in items}
    peer_order = (
        "ABLETON_LIVE",
        "FL_STUDIO",
        "LOGIC_PRO",
        "PRO_TOOLS",
        "STUDIO_ONE",
        "REAPER",
    )
    return HostInstallationInventory(
        os_family="MACOS",
        scan_state=STANDARD_SCAN,
        observations=tuple(items),
        unknown_families=tuple(family for family in peer_order if family not in seen),
    )


def seed(data_root: Path) -> tuple[str, str]:
    headquarters = HeadquartersMemory.create(data_root, "DAW Inventory Artist")
    try:
        song = headquarters.store.create_song("Inventory Song")
        return headquarters.store.profile_id, song.id
    finally:
        headquarters.close()


def test_settings_exposes_positive_installation_truth_without_paths_support_or_authority(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id, song_id = seed(data_root)
    ableton = observation("ABLETON_LIVE", "Ableton Live", "a")
    reaper = observation("REAPER", "REAPER", "b")

    with attached_inventory_surface():
        shell = ConsumerShell(
            data_root=data_root,
            state_root=state_root,
            process=process(),
            probe=Probe(),
        )
        shell._host_installation_scanner = lambda platform: inventory(ableton, reaper)
        shell.start()
        try:
            status, page = request(shell, "/settings")
            assert status == 200
            assert "DAWs on this machine" in page
            assert "Ableton Live" in page
            assert "REAPER" in page
            assert page.count('<span class="status good">Observed locally</span>') == 2
            assert "FL Studio" in page and "UNKNOWN" in page
            assert "does not mean the DAW is open, healthy, adapter-tested, supported, or controllable" in page
            assert "not promoted into Artist or Song memory" in page
            assert ableton.location_fingerprint not in page
            assert reaper.location_fingerprint not in page
            assert str(data_root) not in page and str(state_root) not in page
            assert "/Applications" not in page
            assert 'action="/host' not in page and 'action="/daw' not in page

            check = HeadquartersMemory.open(data_root, profile_id)
            try:
                assert check.store.get_song(song_id) is not None
                assert check.capability_evidence._rows_for_workspace("not-a-workspace") == ()
                assert not any(
                    event.event_type.startswith("HOST_INSTALLATION")
                    for event in check.activity.for_song(song_id)
                )
            finally:
                check.close()
        finally:
            shell.stop()


def test_settings_rescans_ephemerally_instead_of_persisting_old_installation_result(tmp_path: Path) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed(data_root)
    states = [
        inventory(observation("ABLETON_LIVE", "Ableton Live", "c")),
        inventory(observation("FL_STUDIO", "FL Studio", "d")),
    ]
    calls = []

    def scanner(platform):
        calls.append(platform.os_family)
        return states[min(len(calls) - 1, 1)]

    with attached_inventory_surface():
        shell = ConsumerShell(
            data_root=data_root,
            state_root=state_root,
            process=process(),
            probe=Probe(),
        )
        shell._host_installation_scanner = scanner
        shell.start()
        try:
            status, first = request(shell, "/settings")
            assert status == 200
            assert "Ableton Live" in first and "Observed locally" in first

            status, second = request(shell, "/settings")
            assert status == 200
            assert "FL Studio" in second and "Observed locally" in second
            assert "Not observed by this bounded scan, therefore UNKNOWN: Ableton Live" in second
            assert calls == ["MACOS", "MACOS"]
        finally:
            shell.stop()
