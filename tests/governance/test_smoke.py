import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "governance/smoke/consumer_smoke.py"


class SmokeTests(unittest.TestCase):
    def test_current_core01a_consumer_smoke_passes(self):
        cp = subprocess.run(["python", str(SCRIPT)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("CORE-01A CONSUMER SMOKE: GREEN", cp.stdout)

    def test_product_code_is_still_rejected_if_stage_is_preproduct(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            shutil.copytree(ROOT, repo)
            state_path = repo / "governance/current_state.json"
            state = json.loads(state_path.read_text())
            state["active_node"] = "LEGACY-01"
            state.pop("active_increment", None)
            state["product_code_authorized"] = False
            state["legacy_admission_authorized"] = True
            state_path.write_text(json.dumps(state, indent=2) + "\n")
            cp = subprocess.run(
                ["python", str(repo / "governance/smoke/consumer_smoke.py")],
                cwd=repo,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("product implementation appeared early", cp.stderr)


if __name__ == "__main__":
    unittest.main()
