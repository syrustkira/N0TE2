from __future__ import annotations

import hashlib
import json
from typing import Iterable

from .migration import MigrationStep


def migration_steps_payload(steps: Iterable[MigrationStep]) -> list[dict[str, object]]:
    normalized = tuple(steps)
    for step in normalized:
        if not isinstance(step, MigrationStep):
            raise TypeError("schema migration program must contain MigrationStep values")
    return [
        {
            "from_version": step.from_version,
            "to_version": step.to_version,
            "description": step.description,
            "statements": list(step.statements),
        }
        for step in normalized
    ]


def migration_steps_fingerprint(steps: Iterable[MigrationStep]) -> str:
    payload = migration_steps_payload(steps)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
