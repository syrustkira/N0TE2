import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("n0te2_governance", ROOT / "governance/check_governance.py")
gov = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gov)

class GovernanceRegressionTests(unittest.TestCase):
    def clone(self):
        td = tempfile.TemporaryDirectory()
        dst = Path(td.name) / "repo"
        shutil.copytree(
            ROOT,
            dst,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        self.addCleanup(td.cleanup)
        return dst

    def write_json(self, path, data):
        Path(path).write_text(json.dumps(data, indent=2) + "\n")

    def assert_red(self, repo, needle=None):
        with self.assertRaises(gov.GovernanceError) as cm:
            gov.run(repo, verify_git=False)
        if needle:
            self.assertIn(needle, str(cm.exception))

    def test_current_contract_is_green_without_git(self):
        gov.run(ROOT, verify_git=False)

    def test_all_six_daws_plus_generic_are_required(self):
        repo = self.clone()
        p = repo / "governance/completion_graph.json"
        d = json.loads(p.read_text())
        gate = next(n for n in d["nodes"] if n["id"] == "DAW-TEST-READY")
        gate["depends_on"].remove("DAW-06")
        self.write_json(p, d)
        self.assert_red(repo, "DAW-TEST-READY")

    def test_three_platform_gate_cannot_drop_linux(self):
        repo = self.clone()
        p = repo / "governance/completion_graph.json"
        d = json.loads(p.read_text())
        gate = next(n for n in d["nodes"] if n["id"] == "PLATFORM-TEST-READY")
        gate["depends_on"].remove("PLATFORM-03")
        self.write_json(p, d)
        self.assert_red(repo, "platform gate")

    def test_core_architectures_cannot_flatten(self):
        repo = self.clone()
        p = repo / "governance/platform_support.json"
        d = json.loads(p.read_text())
        d["core_architectures"]["macOS"] = ["arm64"]
        self.write_json(p, d)
        self.assert_red(repo, "architecture matrix")

    def test_os_marketing_name_cannot_raise_floor(self):
        repo = self.clone()
        p = repo / "governance/platform_support.json"
        d = json.loads(p.read_text())
        d["policy"]["version_name_alone_is_breakpoint"] = True
        self.write_json(p, d)
        self.assert_red(repo, "marketing name")

    def test_plugins_cannot_collapse_to_vst3(self):
        repo = self.clone()
        p = repo / "governance/plugin_contract.json"
        d = json.loads(p.read_text())
        d["formats"] = {"VST3": d["formats"]["VST3"]}
        self.write_json(p, d)
        self.assert_red(repo, "plugin format model flattened")

    def test_custom_plugin_paths_are_required(self):
        repo = self.clone()
        p = repo / "governance/plugin_contract.json"
        d = json.loads(p.read_text())
        d["ask_for_additional_locations"] = False
        self.write_json(p, d)
        self.assert_red(repo, "additional plugin locations")

    def test_generic_other_is_not_manual_only(self):
        repo = self.clone()
        p = repo / "governance/completion_graph.json"
        d = json.loads(p.read_text())
        node = next(n for n in d["nodes"] if n["id"] == "DAW-07")
        node["depends_on"].remove("AUDIO-02")
        self.write_json(p, d)
        self.assert_red(repo, "manual-only")

    def test_held_scope_cannot_self_activate(self):
        repo = self.clone()
        p = repo / "governance/current_state.json"
        d = json.loads(p.read_text())
        d["active_node"] = "HOLD-001"
        self.write_json(p, d)
        self.assert_red(repo, "held item became active")

    def test_dependency_cycle_is_rejected(self):
        repo = self.clone()
        p = repo / "governance/completion_graph.json"
        d = json.loads(p.read_text())
        root = next(n for n in d["nodes"] if n["id"] == "BOOT-00")
        root["dependency_mode"] = "ALL"
        root["depends_on"] = ["BOOT-02"]
        self.write_json(p, d)
        self.assert_red(repo, "cycle")

    def test_orphan_required_scope_is_rejected(self):
        repo = self.clone()
        p = repo / "governance/completion_graph.json"
        d = json.loads(p.read_text())
        for n in d["nodes"]:
            reqs = [r for r in gov.expand_requirement_expr(n.get("requirements", "")) if r != "REQ-SCOPE-002"]
            n["requirements"] = ",".join(r.rsplit("-", 1)[1] for r in reqs)
        self.write_json(p, d)
        self.assert_red(repo, "orphan active requirements")

    def test_stale_product_north_star_cannot_be_current_authority(self):
        repo = self.clone()
        p = repo / "governance/authority.json"
        d = json.loads(p.read_text())
        d["current_authority_files"].append("PRODUCT_NORTH_STAR.md")
        self.write_json(p, d)
        self.assert_red(repo, "stale authority")

    def test_missing_acceptance_resource_cannot_be_global_stop(self):
        repo = self.clone()
        p = repo / "governance/authority.json"
        d = json.loads(p.read_text())
        d["laws"]["missing_acceptance_resource_stops_unrelated_construction"] = True
        self.write_json(p, d)
        self.assert_red(repo, "resource-wait loop")

    def test_receipt_rejects_product_path_change(self):
        repo = self.clone()
        subprocess.run(["git","init","-b","main"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git","config","user.email","test@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git","config","user.name","N0TE2 Test"], cwd=repo, check=True)
        subprocess.run(["git","add","README.md"], cwd=repo, check=True)
        subprocess.run(["git","commit","-m","seed"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        baseline = subprocess.check_output(["git","rev-parse","HEAD"], cwd=repo, text=True).strip()
        rp = repo / "governance/active_receipt.json"
        receipt = json.loads(rp.read_text()); receipt["baseline_sha"] = baseline; self.write_json(rp, receipt)
        subprocess.run(["git","add","governance","tests",".github","AGENTS.md",".gitignore"], cwd=repo, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git","commit","-am","governance"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git","add","governance","tests","AGENTS.md",".gitignore"], cwd=repo, check=True)
        if subprocess.check_output(["git","status","--porcelain"], cwd=repo, text=True).strip():
            subprocess.run(["git","commit","-m","governance files"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        (repo / "src").mkdir(); (repo / "src/product.py").write_text("print('too early')\n")
        subprocess.run(["git","add","src/product.py"], cwd=repo, check=True)
        subprocess.run(["git","commit","-m","bad product change"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        with self.assertRaises(gov.GovernanceError) as cm:
            gov.run(repo, verify_git=True)
        self.assertIn("outside LEGACY-01 receipt", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
