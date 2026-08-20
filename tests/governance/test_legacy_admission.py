import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("n0te2_governance", ROOT / "governance/check_governance.py")
gov = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gov)


class LegacyAdmissionTests(unittest.TestCase):
    def clone(self):
        td = tempfile.TemporaryDirectory()
        dst = Path(td.name) / "repo"
        shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        self.addCleanup(td.cleanup)
        return dst

    def write_json(self, path, data):
        Path(path).write_text(json.dumps(data, indent=2) + "\n")

    def assert_red(self, repo, needle):
        with self.assertRaises(gov.GovernanceError) as cm:
            gov.run(repo, verify_git=False)
        self.assertIn(needle, str(cm.exception))

    def test_current_legacy_contract_is_green_without_git(self):
        gov.run(ROOT, verify_git=False)

    def test_census_cannot_hide_unclassified_assets(self):
        repo = self.clone()
        p = repo / "governance/legacy_admission.json"
        d = json.loads(p.read_text())
        d["census"]["unclassified_leaf_files"] = 1
        self.write_json(p, d)
        self.assert_red(repo, "unclassified legacy assets")

    def test_family_counts_must_reconcile_to_total(self):
        repo = self.clone()
        p = repo / "governance/legacy_admission.json"
        d = json.loads(p.read_text())
        d["census"]["families"]["native"] -= 1
        self.write_json(p, d)
        self.assert_red(repo, "legacy family counts")

    def test_direct_legacy_source_copy_cannot_self_authorize(self):
        repo = self.clone()
        p = repo / "governance/legacy_admission.json"
        d = json.loads(p.read_text())
        d["rights"]["direct_legacy_source_copy_authorized"] = True
        self.write_json(p, d)
        self.assert_red(repo, "direct legacy source copy")

    def test_direct_legacy_test_text_copy_cannot_self_authorize(self):
        repo = self.clone()
        p = repo / "governance/legacy_admission.json"
        d = json.loads(p.read_text())
        d["rights"]["direct_legacy_test_text_copy_authorized"] = True
        self.write_json(p, d)
        self.assert_red(repo, "direct legacy test text copy")

    def test_license_gate_cannot_disappear(self):
        repo = self.clone()
        p = repo / "governance/legacy_admission.json"
        d = json.loads(p.read_text())
        d["rights"]["license_gate_id"] = ""
        self.write_json(p, d)
        self.assert_red(repo, "LICENSE-GATE-001")

    def test_contamination_scan_cannot_drop_ableton_priority_class(self):
        repo = self.clone()
        p = repo / "governance/legacy_admission.json"
        d = json.loads(p.read_text())
        d["contamination_classes"].remove("ABLETON_SEMANTIC_PRIORITY")
        self.write_json(p, d)
        self.assert_red(repo, "contamination classes")

    def test_legacy_green_cannot_become_product_completion(self):
        repo = self.clone()
        p = repo / "governance/legacy_admission.json"
        d = json.loads(p.read_text())
        d["selected_spec_evidence"]["legacy_green_does_not_close_product_nodes"] = False
        self.write_json(p, d)
        self.assert_red(repo, "legacy green")


if __name__ == "__main__":
    unittest.main()
