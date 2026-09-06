from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CHECKER_PATH = ROOT / "governance" / "check_builder_handoff_intake.py"
BUILDER_PATH = ROOT / "governance" / "builder_handoff.py"

checker_spec = importlib.util.spec_from_file_location("check_builder_handoff_intake", CHECKER_PATH)
checker = importlib.util.module_from_spec(checker_spec)
assert checker_spec.loader is not None
checker_spec.loader.exec_module(checker)

builder_spec = importlib.util.spec_from_file_location("builder_handoff", BUILDER_PATH)
builder = importlib.util.module_from_spec(builder_spec)
assert builder_spec.loader is not None
builder_spec.loader.exec_module(builder)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)
    return git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    baseline = commit(repo, "baseline")
    subprocess.run(["git", "checkout", "-b", "builder/test"], cwd=repo, check=True, capture_output=True)
    return repo, baseline


def complete_declaration() -> dict:
    declaration = builder.template("CANDIDATE-REAL-1", "BUILDER-REAL-1", ["REQ-SCOPE-001"])
    b = declaration["builder_assertions"]
    b["identity"].update(
        product_outcome="Bounded capability",
        artist_job="Use the capability without hidden work loss",
        semantic_owner="REQ-SCOPE-001",
    )
    b["bounded_increment"]["description"] = "One bounded implementation"
    b["semantic_contract"].update(
        required_behavior="Preserve accepted behavior",
        non_negotiable_semantics=["no silent weakening"],
        allowed_implementation_freedom=["internal structure"],
    )
    b["behavior"].update(
        requested_behavior="Implement the capability",
        implemented_behavior="Capability implemented",
    )
    b["behavior"]["consumer_path"] = {
        "applicable": False,
        "reason": "governance-only fixture",
        "path": None,
        "proof": None,
    }
    b["review"]["status"] = "BUILDER_SELF_REVIEWED"
    b["risk"].update(
        tier="TIER_1",
        failure_modes=["handoff omission"],
        rollback_recovery_considerations=["remove candidate"],
    )
    b["steward_handoff"]["dependency_safe_continuation"] = ["disjoint work"]
    b["lenses"] = [
        {"name": "SEMANTIC_CORRECTNESS", "state": "APPLICABLE_PASS"},
        {
            "name": "PUBLIC_DEPLOYMENT",
            "state": "NOT_APPLICABLE_WITH_REASON",
            "reason": "not public-facing",
        },
    ]
    b["validation"]["test_receipts"] = [
        {
            "command": "focused tests",
            "environment": "pre-seal",
            "exact_head": "RUNTIME_EXACT",
            "result": "NOT_RUN",
            "reason": "trusted workflow supplies exact-head CI receipt",
            "observed_at": "2026-09-06T00:00:00Z",
            "artifact_refs": [],
        }
    ]
    return declaration


def set_env(monkeypatch: pytest.MonkeyPatch, baseline: str, head: str, conclusion: str = "success"):
    monkeypatch.setenv("N0TE2_BASE_SHA", baseline)
    monkeypatch.setenv("N0TE2_HEAD_SHA", head)
    monkeypatch.setenv("N0TE2_PR_NUMBER", "999")
    monkeypatch.setenv("N0TE2_BASE_BRANCH", "main")
    monkeypatch.setenv("N0TE2_CI_CONCLUSION", conclusion)
    monkeypatch.setenv("N0TE2_CI_RUN_ID", "12345")
    monkeypatch.setenv("N0TE2_CI_RUN_URL", "https://example.invalid/run/12345")
    monkeypatch.setenv("N0TE2_CI_COMPLETED_AT", "2026-09-06T00:01:00Z")


def write_declaration(repo: Path, name: str = "candidate.builder.json") -> Path:
    path = repo / "governance" / "builder_handoffs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(complete_declaration(), indent=2) + "\n", encoding="utf-8")
    return path


def test_builder_without_declaration_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, baseline = make_repo(tmp_path)
    (repo / "feature.txt").write_text("candidate\n", encoding="utf-8")
    head = commit(repo, "candidate")
    set_env(monkeypatch, baseline, head)
    with pytest.raises(checker.BuilderIntakeError, match="exactly one"):
        checker.qualify(repo)


def test_trusted_ci_overlays_runtime_receipt_and_qualifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, baseline = make_repo(tmp_path)
    (repo / "feature.txt").write_text("candidate\n", encoding="utf-8")
    write_declaration(repo)
    head = commit(repo, "candidate with handoff")
    set_env(monkeypatch, baseline, head, "success")
    result = checker.qualify(repo)
    assert result["computed"]["state"] == "READY_HANDOFF"
    receipt = result["builder_assertions"]["validation"]["test_receipts"][0]
    assert receipt["exact_head"] == head
    assert receipt["result"] == "PASS"
    assert result["authority_verification"]["ci"]["state"] == "PASS"
    assert result["trusted_intake"]["merge_authorization"] is False
    assert result["authority_verification"]["steward"]["state"] == "NOT_EVALUATED"


def test_failed_exact_head_ci_blocks_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, baseline = make_repo(tmp_path)
    write_declaration(repo)
    head = commit(repo, "candidate")
    set_env(monkeypatch, baseline, head, "failure")
    with pytest.raises(checker.BuilderIntakeError, match="trusted exact-head CI did not pass"):
        checker.qualify(repo)


def test_multiple_builder_declarations_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, baseline = make_repo(tmp_path)
    write_declaration(repo, "one.builder.json")
    write_declaration(repo, "two.builder.json")
    head = commit(repo, "ambiguous declarations")
    set_env(monkeypatch, baseline, head)
    with pytest.raises(checker.BuilderIntakeError, match="exactly one"):
        checker.qualify(repo)


def test_incident_repair_is_not_retyped_as_builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, baseline = make_repo(tmp_path)
    receipt_path = repo / "governance" / "active_receipt.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": 7,
                "status": "ACTIVE",
                "repair_kind": "INCIDENT_REPAIR",
                "receipt_id": "REPAIR-1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    head = commit(repo, "incident repair")
    set_env(monkeypatch, baseline, head)
    result = checker.qualify(repo)
    assert result["state"] == "NOT_APPLICABLE_STEWARD_ROLE"
    assert result["candidate_role"] == "STEWARD_INCIDENT_REPAIR"
    assert result["authority_effect"] == "NONE"


def test_meta_enforcement_change_is_not_retyped_as_builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, baseline = make_repo(tmp_path)
    path = repo / ".github" / "workflows" / "builder-handoff-qualification.yml"
    path.parent.mkdir(parents=True)
    path.write_text("name: fixture\n", encoding="utf-8")
    head = commit(repo, "meta governance")
    set_env(monkeypatch, baseline, head)
    result = checker.qualify(repo)
    assert result["candidate_role"] == "STEWARD_META_GOVERNANCE"
    assert result["state"] == "NOT_APPLICABLE_STEWARD_ROLE"
