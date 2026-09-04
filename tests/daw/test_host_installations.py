import dataclasses
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from n0te2.host_installations import (
    INSTALLATION_SOURCE_CLASS,
    NO_STANDARD_SCAN,
    STANDARD_SCAN,
    HostInstallationObservation,
    runtime_host_installation_inventory,
    scan_host_installations,
)
from n0te2.hosts import CORE_HOST_FAMILIES
from n0te2.platforms import PlatformEnvironment


class HostInstallationTests(unittest.TestCase):
    def platform(self, os_name: str):
        return PlatformEnvironment.from_runtime_labels(os_name, "x86_64")

    @staticmethod
    def app(root: Path, relative: str) -> Path:
        path = root / relative
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def exe(root: Path, relative: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
        return path

    def test_macos_standard_applications_detect_all_peer_families_without_paths(self):
        with TemporaryDirectory() as tmp:
            applications = Path(tmp) / "Applications"
            user_applications = Path(tmp) / "User Applications"
            applications.mkdir()
            user_applications.mkdir()
            self.app(applications, "Ableton Live 12 Suite.app")
            self.app(applications, "FL Studio.app")
            self.app(applications, "Logic Pro.app")
            self.app(applications, "Pro Tools.app")
            self.app(applications, "Studio One 7.app")
            self.app(applications, "REAPER.app")

            inventory = scan_host_installations(
                self.platform("Darwin"),
                roots={"APPLICATIONS": applications, "USER_APPLICATIONS": user_applications},
            )

            self.assertEqual(inventory.scan_state, STANDARD_SCAN)
            self.assertEqual(tuple(item.family for item in inventory.observations), CORE_HOST_FAMILIES)
            self.assertEqual(inventory.unknown_families, ())
            self.assertTrue(inventory.absence_is_unknown)
            for item in inventory.observations:
                self.assertEqual(item.source_class, INSTALLATION_SOURCE_CLASS)
                self.assertEqual(item.entry_kind, "APPLICATION_BUNDLE")
                self.assertEqual(len(item.location_fingerprint), 64)
                self.assertNotIn(str(applications), repr(item))

    def test_windows_standard_roots_detect_known_executables_and_leave_logic_unknown(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            program_data = base / "ProgramData"
            program_files = base / "Program Files"
            program_files_x86 = base / "Program Files x86"
            for root in (program_data, program_files, program_files_x86):
                root.mkdir()
            self.exe(program_data, "Ableton/Live 12 Suite/Program/Ableton Live 12 Suite.exe")
            self.exe(program_files, "Image-Line/FL Studio 2025/FL64.exe")
            self.exe(program_files, "Avid/Pro Tools/ProTools.exe")
            self.exe(program_files, "PreSonus/Studio One 7/Studio One.exe")
            self.exe(program_files, "REAPER (x64)/reaper.exe")

            inventory = scan_host_installations(
                self.platform("Windows"),
                roots={
                    "PROGRAMDATA": program_data,
                    "PROGRAMFILES": program_files,
                    "PROGRAMFILES_X86": program_files_x86,
                },
            )

            self.assertEqual(
                tuple(item.family for item in inventory.observations),
                ("ABLETON_LIVE", "FL_STUDIO", "PRO_TOOLS", "STUDIO_ONE", "REAPER"),
            )
            self.assertEqual(inventory.unknown_families, ("LOGIC_PRO",))
            self.assertFalse(inventory.observed("LOGIC_PRO"))

    def test_no_match_is_unknown_not_missing_or_unsupported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = scan_host_installations(
                self.platform("Darwin"),
                roots={"APPLICATIONS": root, "USER_APPLICATIONS": root},
            )
            self.assertEqual(inventory.observations, ())
            self.assertEqual(inventory.unknown_families, CORE_HOST_FAMILIES)
            self.assertTrue(inventory.absence_is_unknown)
            self.assertFalse(hasattr(inventory, "missing_families"))
            self.assertFalse(hasattr(inventory, "unsupported_families"))

    def test_relative_root_cannot_masquerade_as_standard_installation_location(self):
        with TemporaryDirectory() as tmp:
            prior = Path.cwd()
            try:
                os.chdir(tmp)
                applications = Path("Applications")
                applications.mkdir()
                self.app(applications, "Logic Pro.app")
                inventory = scan_host_installations(
                    self.platform("Darwin"),
                    roots={"APPLICATIONS": applications, "USER_APPLICATIONS": applications},
                )
            finally:
                os.chdir(prior)

            self.assertFalse(inventory.observed("LOGIC_PRO"))
            self.assertIn("LOGIC_PRO", inventory.unknown_families)

    def test_linux_has_no_claimed_standard_scan_and_preserves_every_family_as_unknown(self):
        inventory = scan_host_installations(self.platform("Linux"), roots={})
        self.assertEqual(inventory.scan_state, NO_STANDARD_SCAN)
        self.assertEqual(inventory.observations, ())
        self.assertEqual(inventory.unknown_families, CORE_HOST_FAMILIES)

    def test_observation_contract_cannot_smuggle_host_runtime_or_execution_truth(self):
        names = {field.name.casefold() for field in dataclasses.fields(HostInstallationObservation)}
        forbidden_exact = {
            "support",
            "supported",
            "capability",
            "health",
            "preference",
            "process",
            "execution",
            "adapter",
            "host_version",
            "version",
            "edition",
            "path",
            "executable",
        }
        self.assertTrue(names.isdisjoint(forbidden_exact))
        self.assertIn("scan_version", names)

    @unittest.skipIf(os.name == "nt", "Windows CI may not grant symlink creation privilege")
    def test_symlinked_candidate_and_root_escape_cannot_establish_positive_evidence(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            applications = base / "Applications"
            outside = base / "Outside"
            applications.mkdir()
            outside.mkdir()
            real = self.app(outside, "Logic Pro.app")
            (applications / "Logic Pro.app").symlink_to(real, target_is_directory=True)

            inventory = scan_host_installations(
                self.platform("Darwin"),
                roots={"APPLICATIONS": applications, "USER_APPLICATIONS": applications},
            )
            self.assertFalse(inventory.observed("LOGIC_PRO"))
            self.assertIn("LOGIC_PRO", inventory.unknown_families)

    def test_runtime_windows_root_mapping_uses_supplied_environment_without_host_mutation(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            program_data = base / "pd"
            program_files = base / "pf"
            program_files_x86 = base / "pfx86"
            for root in (program_data, program_files, program_files_x86):
                root.mkdir()
            target = self.exe(program_files, "REAPER (x64)/reaper.exe")
            before = target.stat().st_mtime_ns

            inventory = runtime_host_installation_inventory(
                self.platform("Windows"),
                environment={
                    "PROGRAMDATA": str(program_data),
                    "PROGRAMFILES": str(program_files),
                    "PROGRAMFILES(X86)": str(program_files_x86),
                },
                home=base / "home",
            )

            self.assertTrue(inventory.observed("REAPER"))
            self.assertEqual(target.read_bytes(), b"fixture")
            self.assertEqual(target.stat().st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()
