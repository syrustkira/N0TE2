from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "n0te2_incident_repair_exact_paths",
    ROOT / "governance/check_incident_repair_authority.py",
)
authority = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(authority)


def clone_active_repair() -> tuple[tempfile.TemporaryDirectory, Path]:
    td = tempfile.TemporaryDirectory()
    repo = Path(td.name) / "repo"
    shutil.copytree(
        ROOT,
        repo,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    return td, repo


def init_git(repo: Path) -> str:
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
        ["git", "config", "user.name", "N0TE2 Exact Path Test"],
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


def commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def bind_receipt(repo: Path, base: str, *, broad_prefixes: bool) -> None:
    path = repo / "governance/active_receipt.json"
    receipt = json.loads(path.read_text())
    receipt["baseline_sha"] = base
    receipt["allowed_exact_paths"] = ["governance/active_receipt.json"]
    receipt["allowed_prefixes"] = ["governance/"] if broad_prefixes else []
    path.write_text(json.dumps(receipt, indent=2) + "\n")


def run_authority(repo: Path, base: str) -> None:
    with mock.patch.dict(
        os.environ,
        {
            "N0TE2_BASE_SHA": base,
            "N0TE2_EVENT_MODE": "PR",
            "N0TE2_META_GOVERNANCE_REOPEN": "",
        },
        clear=False,
    ):
        authority.run(repo)


def test_current_pr_manifest_satisfies_specialized_repair_authority() -> None:
    receipt = json.loads((ROOT / "governance/active_receipt.json").read_text())
    base = receipt["baseline_sha"]
    with mock.patch.dict(
        os.environ,
        {
            "N0TE2_BASE_SHA": base,
            "N0TE2_EVENT_MODE": "PR",
            "N0TE2_META_GOVERNANCE_REOPEN": "MAIN_STEWARD_LABEL",
        },
        clear=False,
    ):
        authority.run(ROOT)


def test_governance_repair_rejects_broad_path_prefixes() -> None:
    td, repo = clone_active_repair()
    try:
        base = init_git(repo)
        bind_receipt(repo, base, broad_prefixes=True)
        commit(repo, "try broad governance repair prefix")

        with pytest.raises(authority.RepairAuthorityError) as exc:
            run_authority(repo, base)
        assert "must enumerate exact allowed paths" in str(exc.value)
    finally:
        td.cleanup()


def test_governance_repair_rejects_unused_surplus_exact_paths() -> None:
    td, repo = clone_active_repair()
    try:
        base = init_git(repo)
        bind_receipt(repo, base, broad_prefixes=False)
        path = repo / "governance/active_receipt.json"
        receipt = json.loads(path.read_text())
        receipt["allowed_exact_paths"].append("governance/authority.json")
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        commit(repo, "try surplus governance repair authority")

        with pytest.raises(authority.RepairAuthorityError) as exc:
            run_authority(repo, base)
        assert "must exactly equal changed paths" in str(exc.value)
    finally:
        td.cleanup()


def test_governance_repair_accepts_exact_changed_file_manifest() -> None:
    td, repo = clone_active_repair()
    try:
        base = init_git(repo)
        bind_receipt(repo, base, broad_prefixes=False)
        commit(repo, "bind exact governance repair path")

        run_authority(repo, base)
    finally:
        td.cleanup()
