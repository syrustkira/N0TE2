from __future__ import annotations

import pytest

from n0te2.migration import MigrationPlanError, MigrationStep
from n0te2.schema_program import (
    migration_steps_fingerprint,
    select_migration_chain,
)


def edge(source: int, target: int) -> MigrationStep:
    return MigrationStep(
        source,
        target,
        f"schema {source} to {target}",
        (f"CREATE TABLE marker_{target}(value TEXT)",),
    )


def test_release_program_can_serve_multiple_source_schema_versions() -> None:
    one_two = edge(1, 2)
    two_three = edge(2, 3)
    program = (one_two, two_three)

    assert select_migration_chain(
        source_version=1, target_version=3, program=program
    ) == program
    assert select_migration_chain(
        source_version=2, target_version=3, program=program
    ) == (two_three,)
    assert select_migration_chain(
        source_version=3, target_version=3, program=program
    ) == ()


def test_profile_subset_does_not_change_authenticated_release_program_fingerprint() -> None:
    program = (edge(1, 2), edge(2, 3))
    authenticated = migration_steps_fingerprint(program)
    selected = select_migration_chain(
        source_version=2, target_version=3, program=program
    )

    assert migration_steps_fingerprint(program) == authenticated
    assert migration_steps_fingerprint(selected) != authenticated


def test_program_must_be_ordered_contiguous_and_terminate_at_release_target() -> None:
    one_two = edge(1, 2)
    two_three = edge(2, 3)
    three_four = edge(3, 4)

    with pytest.raises(MigrationPlanError, match="ordered and contiguous"):
        migration_steps_fingerprint((two_three, one_two))
    with pytest.raises(MigrationPlanError, match="terminate at target_version"):
        select_migration_chain(
            source_version=1,
            target_version=3,
            program=(one_two, two_three, three_four),
        )
    with pytest.raises(MigrationPlanError, match="no contiguous migration path"):
        select_migration_chain(
            source_version=1,
            target_version=3,
            program=(two_three,),
        )
