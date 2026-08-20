import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("n0te2_governance_increment", ROOT / "governance/check_governance.py")
gov = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gov)


class ProductIncrementReceiptTests(unittest.TestCase):
    def clone(self):
        td = tempfile.TemporaryDirectory()
        dst = Path(td.name) / "repo"
        shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        self.addCleanup(td.cleanup)
        return dst

    @staticmethod
    def write(path, data):
        path.write_text(json.dumps(data, indent=2) + "\n")

    def test_receipt_increment_must_match_current_increment(self):
        repo = self.clone()
        path = repo / "governance/active_receipt.json"
        receipt = json.loads(path.read_text())
        receipt["increment_id"] = "CORE-01A"
        self.write(path, receipt)
        with self.assertRaises(gov.GovernanceError) as cm:
            gov.run(repo, verify_git=False)
        self.assertIn("receipt increment must match", str(cm.exception))

    def test_increment_cannot_claim_a_different_parent_node(self):
        repo = self.clone()
        state_path = repo / "governance/current_state.json"
        receipt_path = repo / "governance/active_receipt.json"
        state = json.loads(state_path.read_text())
        receipt = json.loads(receipt_path.read_text())
        state["active_increment"] = "SONG-01A"
        receipt["increment_id"] = "SONG-01A"
        receipt["receipt_id"] = "N0TE2-SONG-01A"
        self.write(state_path, state)
        self.write(receipt_path, receipt)
        with self.assertRaises(gov.GovernanceError) as cm:
            gov.run(repo, verify_git=False)
        self.assertIn("must belong to active node CORE-01", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
