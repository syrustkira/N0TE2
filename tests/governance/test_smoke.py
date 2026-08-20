import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "governance/smoke/consumer_smoke.py"

class SmokeTests(unittest.TestCase):
    def test_governance_only_surface_passes(self):
        cp = subprocess.run(["python", str(SCRIPT)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("GREEN", cp.stdout)

    def test_product_code_is_rejected_during_boot02(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            shutil.copytree(ROOT, repo)
            (repo / "src").mkdir()
            (repo / "src/product.py").write_text("x=1\n")
            cp = subprocess.run(["python", str(repo / "governance/smoke/consumer_smoke.py")], cwd=repo, text=True, capture_output=True)
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("product implementation appeared early", cp.stderr)

if __name__ == "__main__":
    unittest.main()
