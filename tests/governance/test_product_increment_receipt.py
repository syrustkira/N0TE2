import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "n0te2_governance_increment", ROOT / "governance/check_governance.py"
)
gov = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gov)


class ProductIncrementReceiptTests(unittest.TestCase):
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
    def write(path, data):
        path.write_text(json.dumps(data, indent=2) + "\n")

    def test_receipt_increment_must_match_current_increment(self):
        repo = self.clone()
        path = repo / "governance/active_receipt.json"
        receipt = json.loads(path.read_text())
        receipt["increment_id"] = "WRONG-INCREMENT"
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
        active = state["active_node"]
        state["active_increment"] = "DAW-00-UNRELATED"
        receipt["increment_id"] = "DAW-00-UNRELATED"
        receipt["receipt_id"] = "N0TE2-DAW-00-UNRELATED"
        self.write(state_path, state)
        self.write(receipt_path, receipt)
        with self.assertRaises(gov.GovernanceError) as cm:
            gov.run(repo, verify_git=False)
        self.assertIn(f"must belong to active node {active}", str(cm.exception))

    def test_active_increment_is_explicitly_parent_bound(self):
        repo = self.clone()
        state = json.loads((repo / "governance/current_state.json").read_text())
        increment = str(state["active_increment"])
        self.assertTrue(
            increment.startswith(str(state["active_node"])),
            f"{increment} must remain explicitly bound to {state['active_node']}",
        )
        gov.run(repo, verify_git=False)


if __name__ == "__main__":
    unittest.main()
