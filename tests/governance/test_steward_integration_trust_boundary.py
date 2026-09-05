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
    "n0te2_steward_integration_trust_boundary",
    ROOT / "governance/check_steward_integration.py",
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class StewardIntegrationTrustBoundaryTests(unittest.TestCase):
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
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "N0TE2 Steward Test"], cwd=repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    @staticmethod
    def commit(repo, message):
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    def run_gate(self, repo, baseline, **extra_env):
        env = {
            "N0TE2_BASE_SHA": baseline,
            "N0TE2_HEAD_SHA": "",
            "N0TE2_EVENT_MODE": "",
            "N0TE2_DIFF_MODE": "PR_MERGE_BASE",
            "N0TE2_PR_NUMBER": "",
        }
        env.update(extra_env)
        with mock.patch.dict(os.environ, env, clear=False):
            gate.run(repo, verify_git=True)

    def test_selected_false_cannot_be_retyped_as_zero(self):
        repo = self.clone()
        path = repo / "governance/requirements.json"
        doc = json.loads(path.read_text())
        doc["canonical_extensions"][0]["selected"] = 0
        self.write_json(path, doc)
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            gate.run(repo, verify_git=False)
        self.assertIn("JSON types", str(cm.exception))

    def test_scope_selection_contract_cannot_be_weakened(self):
        repo = self.clone()
        path = repo / "governance/requirements.json"
        doc = json.loads(path.read_text())
        doc["known_selects_work"] = True
        self.write_json(path, doc)
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            gate.run(repo, verify_git=False)
        self.assertIn("selection contract", str(cm.exception))

    def test_unknown_lifecycle_fails_before_diff_gate(self):
        repo = self.clone()
        path = repo / "governance/current_state.json"
        current = json.loads(path.read_text())
        current["lifecycle_state"] = "STABL"
        self.write_json(path, current)
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            gate.run(repo, verify_git=False)
        self.assertIn("unrecognized lifecycle_state", str(cm.exception))

    @unittest.skipIf(os.name == "nt", "symlink creation is not portable on Windows runners")
    def test_symlinked_candidate_governance_input_fails_closed(self):
        repo = self.clone()
        target = repo / "governance/merge_policy.json"
        target.unlink()
        target.symlink_to(Path("../requirements.json"))
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            gate.run(repo, verify_git=False)
        self.assertIn("must not be a symlink", str(cm.exception))

    def test_lowercase_open_incident_cannot_bypass_scope_requirement(self):
        repo = self.clone()
        path = repo / "governance/incidents.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        rows.append({"id": "INC-LOWERCASE-OPEN", "status": "  open  ", "summary": "must block"})
        path.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n")
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            gate.run(repo, verify_git=False)
        self.assertIn("lacks blocking_scope", str(cm.exception))

    def test_base_incident_history_cannot_be_mutated(self):
        repo = self.clone()
        baseline = self.init_git(repo)
        path = repo / "governance/incidents.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        rows[0]["summary"] = "rewritten history"
        path.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n")
        self.commit(repo, "rewrite incident history")
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            self.run_gate(repo, baseline)
        self.assertIn("mutated durable incident history", str(cm.exception))

    def test_unicode_product_path_is_detected_without_git_quoting_escape(self):
        repo = self.clone()
        baseline = self.init_git(repo)
        (repo / "n0te2/café.py").write_text("VALUE = 1\n")
        self.commit(repo, "unicode product path")
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            self.run_gate(repo, baseline)
        self.assertIn("café.py", str(cm.exception))

    @unittest.skipIf(os.name == "nt", "case-variant directory cannot be represented independently on Windows")
    def test_case_variant_product_directory_is_construction_sensitive(self):
        repo = self.clone()
        baseline = self.init_git(repo)
        (repo / "N0TE2").mkdir()
        (repo / "N0TE2/windows_only.py").write_text("VALUE = 1\n")
        self.commit(repo, "case variant product path")
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            self.run_gate(repo, baseline)
        self.assertIn("N0TE2/windows_only.py", str(cm.exception))

    def test_exact_tree_mode_detects_non_fast_forward_product_deletion(self):
        repo = self.clone()
        root = self.init_git(repo)
        (repo / "n0te2/reset_probe.py").write_text("VALUE = 1\n")
        prior_main = self.commit(repo, "main gained product file")
        subprocess.run(["git", "reset", "--hard", root], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            self.run_gate(repo, prior_main, N0TE2_DIFF_MODE="EXACT_TREE")
        self.assertIn("reset_probe.py", str(cm.exception))

    def test_required_platform_contexts_are_pinned(self):
        repo = self.clone()
        path = repo / "governance/merge_policy.json"
        policy = json.loads(path.read_text())
        policy["required_exact_head_status_contexts"] = ["n0te2-governance-Linux"]
        self.write_json(path, policy)
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            gate.run(repo, verify_git=False)
        self.assertIn("platform contexts changed", str(cm.exception))

    def test_handoff_rule_cannot_reclaim_lifecycle_authority(self):
        repo = self.clone()
        path = repo / "governance/handoff.json"
        handoff = json.loads(path.read_text())
        handoff["derived_runtime_truth"]["rule"] = "Handoff owns lifecycle now."
        self.write_json(path, handoff)
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            gate.run(repo, verify_git=False)
        self.assertIn("lifecycle ownership declaration changed", str(cm.exception))

    def test_steward_actor_allowed_mutations_cannot_expand(self):
        repo = self.clone()
        path = repo / "governance/automation_registry.json"
        registry = json.loads(path.read_text())
        actor = next(row for row in registry["actors"] if row["id"] == "AUTO-STEWARD-INTEGRATION-GATE-001")
        actor["allowed_mutations"] = ["CI_STATUS_CONTEXT", "MERGE_MAIN"]
        self.write_json(path, registry)
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            gate.run(repo, verify_git=False)
        self.assertIn("authority contract drifted", str(cm.exception))

    def test_ordinary_pr_cannot_mutate_trusted_gate_artifact(self):
        repo = self.clone()
        baseline = self.init_git(repo)
        path = repo / ".github/workflows/governance.yml"
        path.write_text(path.read_text() + "\n# unauthorized gate mutation\n")
        self.commit(repo, "change trusted workflow")
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            self.run_gate(repo, baseline, N0TE2_EVENT_MODE="PR", N0TE2_PR_NUMBER="211")
        self.assertIn("trusted gate artifact changed", str(cm.exception))

    def test_blocking_206_rejects_unrelated_pr(self):
        repo = self.clone()
        baseline = self.init_git(repo)
        (repo / "governance/unrelated_probe.txt").write_text("not an incident repair\n")
        self.commit(repo, "unrelated governance change")
        with self.assertRaises(gate.StewardIntegrationError) as cm:
            self.run_gate(repo, baseline, N0TE2_EVENT_MODE="PR", N0TE2_PR_NUMBER="999")
        self.assertIn("permits only its explicit bootstrap", str(cm.exception))

    def test_211_bootstrap_is_the_only_terminal_pr_exception_for_206(self):
        repo = self.clone()
        baseline = self.init_git(repo)
        (repo / "governance/bootstrap_probe.txt").write_text("bounded bootstrap probe\n")
        self.commit(repo, "steward bootstrap repair")
        self.run_gate(repo, baseline, N0TE2_EVENT_MODE="PR", N0TE2_PR_NUMBER="211")

    def test_candidate_executing_workflow_has_no_status_write_token(self):
        workflow = (ROOT / ".github/workflows/governance.yml").read_text()
        self.assertNotIn("statuses: write", workflow)
        self.assertNotIn("checks: write", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("n0te2-governance-Linux", workflow)
        self.assertIn("n0te2-governance-Windows", workflow)
        self.assertIn("n0te2-governance-macOS", workflow)


if __name__ == "__main__":
    unittest.main()
