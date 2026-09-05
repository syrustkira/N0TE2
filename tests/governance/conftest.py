"""Governance-test process configuration.

Synthetic Git repositories are created by several governance regression tests.
On macOS, Git's automatic maintenance can briefly continue touching `.git`
after an assertion has completed, racing `TemporaryDirectory` cleanup and
turning a passing governance test into an infrastructure-only teardown error.

Disable only automatic Git maintenance for Git subprocesses spawned by this
pytest process. Explicit Git operations remain unchanged, and production/runtime
Git behavior is untouched.
"""

from __future__ import annotations

import os


def _append_git_config(key: str, value: str) -> None:
    count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
    os.environ[f"GIT_CONFIG_KEY_{count}"] = key
    os.environ[f"GIT_CONFIG_VALUE_{count}"] = value
    os.environ["GIT_CONFIG_COUNT"] = str(count + 1)


_append_git_config("gc.auto", "0")
_append_git_config("maintenance.auto", "false")
