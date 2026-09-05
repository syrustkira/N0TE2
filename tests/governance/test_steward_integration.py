import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "n0te2_steward_integration", ROOT / "governance/check_steward_integration.py"
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class StewardIntegrationGateTests(unittest.TestCase):
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

    @staticmethod
    def write_json(path, data):
        Path(path).write_text(json.dumps(data, indent=2) + "\n")

    @staticmethod
    def init_git(repo):
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=repo,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "N0TE2 Steward Test"],
            cwd=repo,
            check=True,
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "seed"],
            cwd=repo,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()

    def test_current_contract_is_green_without_git(self):
        gate.run(ROOT, verify_git=False)

    def test_canonical_scope_cannot_shrink_below_retained_170(self):
        repo = self.clone()
        path = repo / "governance/requirements.json"
        doc = json.loads(path.read_text())
        doc["canonical_scope"]["end"] = 169
        doc["canonical_scope"]["retained_requirement_count"] = 168
        doc["canonical_extensions"] = [
            row for row in doc["canonical_extensions"] if row["id"] != "REQ-SCOPE-170"
        ]
        self.write_json(path, doc)
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            gate.run(repo, verify_git=False)
        self.assertIn("cannot shrink below", str(cm.exception))

    def test_canonical_extension_affinity_must_reference_real_graph_node(self):
        repo = self.clone()
        path = repo / "governance/requirements.json"
        doc = json.loads(path.read_text())
        doc["canonical_extensions"][0]["construction_affinity"] = ["NOT-A-NODE"]
        self.write_json(path, doc)
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            gate.run(repo, verify_git=False)
        self.assertIn("unknown construction-affinity node", str(cm.exception))

    def test_stable_candidate_cannot_add_product_code_without_active_receipt(self):
        repo = self.clone()
        baseline = self.init_git(repo)
        product = repo / "n0te2/steward_unauthorized_probe.py"
        product.write_text("VALUE = 'must be rejected'\n")
        subprocess.run(["git", "add", str(product.relative_to(repo))], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "unauthorized product construction"],
            cwd=repo,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        with mock.patch.dict(os.environ, {"N0TE2_BASE_SHA": baseline}, clear=False):
            with self.assertRaises(gate.StewardIntegrationError) as cm:
                gate.run(repo, verify_git=True)
        self.assertIn("STABLE candidate changed construction-sensitive paths", str(cm.exception))

    def test_stable_governance_only_repair_is_allowed(self):
        repo = self.clone()
        baseline = self.init_git(repo)
        probe = repo / "governance/steward_probe.txt"
        probe.write_text("integration-only governance repair\n")
        subprocess.run(["git", "add", str(probe.relative_to(repo))], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "governance-only repair"],
            cwd=repo,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        with mock.patch.dict(os.environ, {"N0TE2_BASE_SHA": baseline}, clear=False):
            gate.run(repo, verify_git=True)

    def test_pending_review_cannot_be_removed_from_merge_policy(self):
        repo = self.clone()
        path = repo / "governance/merge_policy.json"
        policy = json.loads(path.read_text())
        policy["steward_gate"]["pending_review_blocks_merge"] = False
        self.write_json(path, policy)
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            gate.run(repo, verify_git=False)
        self.assertIn("pending substantive review", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
