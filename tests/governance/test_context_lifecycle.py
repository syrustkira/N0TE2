import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HANDOFF_SPEC = importlib.util.spec_from_file_location(
    "n0te2_context_handoff",
    ROOT / "governance/build_handoff.py",
)
handoff_mod = importlib.util.module_from_spec(HANDOFF_SPEC)
HANDOFF_SPEC.loader.exec_module(handoff_mod)


class ContextLifecycleGovernanceTests(unittest.TestCase):
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
    def write_json(path, payload):
        Path(path).write_text(json.dumps(payload, indent=2) + "\n")

    def run_checker(self, repo):
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "governance/check_context_lifecycle.py"),
                "--repo",
                str(repo),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_red(self, repo, needle):
        result = self.run_checker(repo)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(needle, result.stderr)

    def test_context_governance_is_green(self):
        result = self.run_checker(ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("N0TE2 CONTEXT GOVERNANCE: GREEN", result.stdout)

    def test_exact_supervision_doctrine_cannot_drift(self):
        repo = self.clone()
        path = repo / "governance/invariants.json"
        payload = json.loads(path.read_text())
        row = next(row for row in payload["constitutional"] if row["id"] == "INV-SUP-003")
        row["statement"] = "Keep most loops justified most of the time."
        self.write_json(path, payload)
        self.assert_red(repo, "required invariant text drifted: INV-SUP-003")

    def test_projection_cannot_become_canonical_authority(self):
        repo = self.clone()
        path = repo / "governance/context_lifecycle.json"
        payload = json.loads(path.read_text())
        payload["projection_contract"]["canonical_sources_remain_authoritative"] = False
        self.write_json(path, payload)
        self.assert_red(repo, "projection became canonical authority")

    def test_conversation_cannot_auto_promote(self):
        repo = self.clone()
        path = repo / "governance/context_lifecycle.json"
        payload = json.loads(path.read_text())
        payload["conversation_distillation"]["automatic_durable_promotion"] = True
        self.write_json(path, payload)
        self.assert_red(repo, "conversation may not auto-promote")

    def test_semantic_gc_cannot_delete_history_by_default(self):
        repo = self.clone()
        path = repo / "governance/context_lifecycle.json"
        payload = json.loads(path.read_text())
        payload["semantic_gc"]["delete_canonical_history_by_default"] = True
        self.write_json(path, payload)
        self.assert_red(repo, "may not delete canonical history")

    def test_automation_requires_bounded_failure_policy(self):
        repo = self.clone()
        path = repo / "governance/automation_registry.json"
        payload = json.loads(path.read_text())
        payload["actors"][0]["failure_policy"]["max_retries"] = -1
        self.write_json(path, payload)
        self.assert_red(repo, "requires bounded max_retries")

    def test_superseded_ci_work_must_be_cancelled(self):
        repo = self.clone()
        path = repo / ".github/workflows/governance.yml"
        workflow = path.read_text(encoding="utf-8")
        path.write_text(
            workflow.replace("cancel-in-progress: true", "cancel-in-progress: false"),
            encoding="utf-8",
        )
        self.assert_red(
            repo,
            "superseded governance CI may continue running without justification",
        )

    def test_fresh_agent_reconstructs_without_prior_chat(self):
        with mock.patch.object(handoff_mod, "git", return_value="a" * 40):
            runtime = handoff_mod.build_runtime_handoff(ROOT)
        self.assertFalse(runtime["fresh_agent_requires_prior_chat"])
        self.assertEqual(runtime["lifecycle"]["active_increment"], "UX-01-CONTEXT-LIFECYCLE-01")
        self.assertEqual(runtime["supervision"]["root"], "N0TE-SUPERVISOR")
        self.assertEqual(runtime["supervision"]["context_policy"]["id"], "CTX-LIFECYCLE-001")
        self.assertIn("OPEN_INCIDENTS", runtime["required_reconstruction_outcomes"])
        self.assertIn("AUTOMATION_SUPERVISION", runtime["required_reconstruction_outcomes"])
        self.assertIn("CONTEXT_POLICY", runtime["required_reconstruction_outcomes"])
        self.assertEqual(
            runtime["archaeology_fallback"]["allowed_only_for"],
            ["MISSING_DURABLE_AUTHORITY", "CONTRADICTORY_DURABLE_AUTHORITY"],
        )


if __name__ == "__main__":
    unittest.main()
