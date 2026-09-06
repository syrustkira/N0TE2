from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[2] / "governance" / "builder_handoff.py"
spec = importlib.util.spec_from_file_location("builder_handoff", MODULE_PATH)
builder_handoff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(builder_handoff)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "builder@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Builder Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("baseline\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True)
    baseline = git(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "checkout", "-b", "builder/CANDIDATE-1"], cwd=repo, check=True, capture_output=True)
    return repo, baseline


def complete_declaration(candidate_id: str = "CANDIDATE-1", tier: str = "TIER_1") -> dict:
    d = builder_handoff.template(candidate_id, "BUILDER-1", ["REQ-SCOPE-001"])
    b = d["builder_assertions"]
    b["identity"].update(
        product_outcome="A bounded outcome",
        artist_job="Use the capability without hidden reconstruction",
        semantic_owner="REQ-SCOPE-001",
    )
    b["bounded_increment"]["description"] = "Bounded implementation"
    b["semantic_contract"].update(
        required_behavior="Preserve exact accepted behavior",
        non_negotiable_semantics=["no silent requirement weakening"],
        allowed_implementation_freedom=["internal structure"],
    )
    b["behavior"].update(
        requested_behavior="Create the bounded capability",
        implemented_behavior="Capability implemented",
    )
    b["behavior"]["consumer_path"] = {
        "applicable": False,
        "reason": "governance-only increment",
        "path": None,
        "proof": None,
    }
    b["review"]["status"] = "BUILDER_SELF_REVIEWED"
    b["risk"].update(
        tier=tier,
        failure_modes=["handoff omission"],
        rollback_recovery_considerations=["remove candidate commit"],
    )
    b["steward_handoff"]["dependency_safe_continuation"] = ["disjoint documentation"]
    b["lenses"] = [
        {"name": "SEMANTIC_CORRECTNESS", "state": "APPLICABLE_PASS", "reason": "structured proof"},
        {"name": "PUBLIC_DEPLOYMENT", "state": "NOT_APPLICABLE_WITH_REASON", "reason": "not public-facing"},
    ]
    return d


def commit_candidate(repo: Path, name: str = "feature.txt") -> str:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("candidate\n")
    subprocess.run(["git", "add", name], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "candidate"], cwd=repo, check=True, capture_output=True)
    return git(repo, "rev-parse", "HEAD")


def seal(repo: Path, baseline: str, declaration: dict) -> dict:
    head = git(repo, "rev-parse", "HEAD")
    if declaration["builder_assertions"]["risk"]["tier"] != "TIER_0":
        declaration["builder_assertions"]["validation"]["test_receipts"] = [
            {
                "command": "pytest -q",
                "environment": "test",
                "exact_head": "RUNTIME_EXACT",
                "result": "PASS",
                "observed_at": "2026-09-05T00:00:00Z",
                "artifact_refs": ["pytest-results.xml"],
            }
        ]
    runtime = builder_handoff.collect(repo, baseline, baseline, "main")
    manifest = builder_handoff.seal(declaration, runtime, expected_head=head)
    return manifest


def test_builder_handoff_tooling_is_read_only_and_refuses_main(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
    before = git(repo, "rev-parse", "HEAD")
    with pytest.raises(builder_handoff.BuilderHandoffError, match="refuses to run from main"):
        builder_handoff.collect(repo, baseline, baseline, "main")
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "status", "--porcelain") == ""


def test_exact_head_is_captured_and_new_push_stales_prior_handoff(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    head = commit_candidate(repo)
    manifest = seal(repo, baseline, complete_declaration())
    assert manifest["runtime_binding"]["candidate"]["head_sha"] == head
    assert manifest["computed"]["state"] == "READY_HANDOFF"
    commit_candidate(repo, "later.txt")
    new_head = git(repo, "rev-parse", "HEAD")
    findings = builder_handoff.validate(manifest, current_head=new_head)
    assert any("exact-head binding is stale" in finding for finding in findings)


def test_missing_semantic_identity_blocks_ready_handoff(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo)
    declaration = complete_declaration()
    declaration["builder_assertions"]["identity"]["semantic_owner"] = ""
    manifest = seal(repo, baseline, declaration)
    assert manifest["computed"]["state"] == "HANDOFF_BLOCKED"
    assert any("semantic_owner" in finding for finding in manifest["computed"]["findings"])


def test_tier_three_requires_stronger_evidence(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo)
    declaration = complete_declaration(tier="TIER_3")
    manifest = seal(repo, baseline, declaration)
    assert manifest["computed"]["state"] == "HANDOFF_BLOCKED"
    assert any("full_regression" in finding for finding in manifest["computed"]["findings"])
    assert any("recovery_proof" in finding for finding in manifest["computed"]["findings"])


def test_tier_zero_does_not_require_irrelevant_provider_or_test_fields(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo, "docs.txt")
    declaration = complete_declaration(tier="TIER_0")
    declaration["builder_assertions"]["validation"]["test_receipts"] = []
    declaration["builder_assertions"]["behavior"].pop("provider_behavior")
    manifest = seal(repo, baseline, declaration)
    assert manifest["computed"]["state"] == "READY_HANDOFF"


def test_partial_implementation_requires_durable_successor_residue(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo)
    declaration = complete_declaration()
    declaration["builder_assertions"]["limitations"]["deferred_portions"] = [
        {"description": "remaining public acceptance"}
    ]
    manifest = seal(repo, baseline, declaration)
    assert manifest["computed"]["state"] == "HANDOFF_BLOCKED"
    assert any("deferred_portions[0] missing fields" in f for f in manifest["computed"]["findings"])


def test_public_impact_cannot_remain_undeclared(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo)
    declaration = complete_declaration()
    declaration["builder_assertions"]["public_consequence"] = {}
    manifest = seal(repo, baseline, declaration)
    assert any("public impact must be explicitly" in f for f in manifest["computed"]["findings"])


def test_builder_cannot_self_grant_acceptance_or_rights(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo)
    declaration = complete_declaration()
    declaration["builder_assertions"]["steward_handoff"]["human_accepted"] = True
    declaration["builder_assertions"]["public_consequence"]["rights_cleared"] = True
    declaration["builder_assertions"]["review"]["status"] = "PUBLIC_VERIFIED"
    manifest = seal(repo, baseline, declaration)
    joined = "\n".join(manifest["computed"]["findings"])
    assert "human_accepted" in joined
    assert "rights_cleared" in joined
    assert "PUBLIC_VERIFIED" in joined


def test_rights_sensitive_asset_requires_provenance_record(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo, "artwork.png")
    declaration = complete_declaration()
    manifest = seal(repo, baseline, declaration)
    assert any("rights/provenance-sensitive files changed" in f for f in manifest["computed"]["findings"])


def test_schema_migration_requires_migration_and_recovery_proof(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo, "migrations/001.sql")
    declaration = complete_declaration()
    manifest = seal(repo, baseline, declaration)
    joined = "\n".join(manifest["computed"]["findings"])
    assert "migration_proof" in joined
    assert "recovery_proof" in joined


def test_fix_order_creates_fresh_handoff_lineage(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo)
    previous = seal(repo, baseline, complete_declaration())
    successor = builder_handoff.successor(previous, "FIX_ORDER", "FIX-001")
    assert successor["handoff_version"] == "H1"
    assert successor["builder_assertions"]["lineage"]["previous_handoff_version"] == "H0"
    assert successor["builder_assertions"]["lineage"]["previous_head_sha"] == previous["runtime_binding"]["candidate"]["head_sha"]
    assert successor["builder_assertions"]["lineage"]["order"]["order_id"] == "FIX-001"
    assert successor["builder_assertions"]["validation"]["test_receipts"] == []


def test_rebuild_order_preserves_original_requirement_lineage(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo)
    previous = seal(repo, baseline, complete_declaration())
    successor = builder_handoff.successor(previous, "REBUILD_ORDER", "REBUILD-001")
    assert successor["builder_assertions"]["identity"]["requirement_ids"] == ["REQ-SCOPE-001"]
    assert successor["builder_assertions"]["lineage"]["order"]["type"] == "REBUILD_ORDER"


def test_split_required_is_machine_visible(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo)
    declaration = complete_declaration()
    declaration["builder_assertions"]["bounded_increment"]["unrelated_concerns"] = True
    manifest = seal(repo, baseline, declaration)
    assert manifest["computed"]["state"] == "SPLIT_REQUIRED"
    assert manifest["computed"]["split_signal"] == "SPLIT_REQUIRED"


def test_builder_is_explicitly_released_to_dependency_safe_work(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo)
    manifest = seal(repo, baseline, complete_declaration())
    assert manifest["builder_assertions"]["steward_handoff"]["dependency_safe_continuation"]
    assert manifest["computed"]["state"] == "READY_HANDOFF"


def test_multiple_builders_can_intake_concurrently_without_shared_mutable_index(tmp_path: Path):
    manifests = tmp_path / "handoffs"
    manifests.mkdir()
    for number in (1, 2):
        repo, baseline = make_repo(tmp_path / f"r{number}")
        commit_candidate(repo)
        manifest = seal(repo, baseline, complete_declaration(candidate_id=f"CANDIDATE-{number}"))
        (manifests / f"candidate-{number}.json").write_text(json.dumps(manifest))
    view = builder_handoff.intake([manifests])
    assert {item["candidate_id"] for item in view["candidates"]} == {"CANDIDATE-1", "CANDIDATE-2"}
    assert view["invalid"] == []


def test_intake_never_promotes_builder_to_steward_authority(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo)
    manifest = seal(repo, baseline, complete_declaration())
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    view = builder_handoff.intake([path])
    assert view["candidates"][0]["state"] == "READY_HANDOFF"
    assert manifest["authority_verification"]["steward"]["state"] == "NOT_EVALUATED"
    assert manifest["authority_verification"]["steward"]["disposition"] is None


def test_auditor_traces_candidate_requirement_head_and_disposition(tmp_path: Path):
    repo, baseline = make_repo(tmp_path)
    commit_candidate(repo)
    manifest = seal(repo, baseline, complete_declaration())
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    result = builder_handoff.audit([path], dispositions={"CANDIDATE-1": "QUALIFYING"})
    trace = result["traces"][0]
    assert trace["candidate_id"] == "CANDIDATE-1"
    assert trace["requirement_ids"] == ["REQ-SCOPE-001"]
    assert trace["head_sha"] == manifest["runtime_binding"]["candidate"]["head_sha"]
    assert trace["steward_disposition"] == "QUALIFYING"


def test_migration_classification_does_not_fabricate_missing_evidence():
    assert builder_handoff.migration_classify({"semantic_identity": True, "exact_head": True}) == "RECONSTRUCTABLE"
    assert builder_handoff.migration_classify({"head_stale": True, "semantic_identity": True}) == "STALE"
    assert builder_handoff.migration_classify({"superseded": True}) == "SUPERSEDED"
