from __future__ import annotations

import os
from pathlib import Path

from .memory import HeadquartersMemory
from .update import (
    ApplicationUpdateCoordinator as _BaseApplicationUpdateCoordinator,
    ApplicationUpdateError,
    UpdateRejectedError,
    UpdateResult,
)


class _ValidationDatabaseOwnershipUncertain(BaseException):
    """Bypass automatic compensation when the validation database may still be open."""


def _canonical_data_root(path: str | Path) -> Path:
    root = Path(path)
    if not root.is_absolute():
        raise ApplicationUpdateError("data_root must be absolute")
    lexical = Path(os.path.abspath(os.path.normpath(str(root))))
    try:
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise UpdateRejectedError(
            "data_root must exist and resolve to a real directory"
        ) from exc
    if resolved != lexical:
        raise UpdateRejectedError(
            "data_root must not traverse a symlink or filesystem alias during update"
        )
    if not resolved.is_dir():
        raise UpdateRejectedError("data_root must resolve to a directory")
    return resolved


class ApplicationUpdateCoordinator(_BaseApplicationUpdateCoordinator):
    """Consumer-safe APP-01C update coordinator.

    The durable transaction/state machine lives in ``n0te2.update``. This thin
    guard adds two fail-closed boundaries: update roots must resolve to one
    canonical real directory, and package/data compensation is forbidden when
    post-install validation cannot prove the canonical database was released.
    """

    def prepare(self, *, runtime, **kwargs):  # type: ignore[override]
        _canonical_data_root(runtime.data_root)
        return super().prepare(runtime=runtime, **kwargs)

    def _validate_installed_headquarters(
        self,
        data_root: Path,
        profile_id: str,
    ) -> None:
        headquarters: HeadquartersMemory | None = None
        validation_error: Exception | None = None
        try:
            headquarters = self._memory_opener(data_root, profile_id)
            if not isinstance(headquarters, HeadquartersMemory):
                raise UpdateRejectedError("memory_opener did not return HeadquartersMemory")
            if headquarters.store.profile_id != profile_id:
                raise UpdateRejectedError("updated Headquarters opened a different profile")
            quick = headquarters.store._conn.execute("PRAGMA quick_check").fetchone()
            foreign = headquarters.store._conn.execute("PRAGMA foreign_key_check").fetchall()
            if quick is None or str(quick[0]) != "ok" or foreign:
                raise UpdateRejectedError(
                    "post-update canonical database integrity check failed"
                )
        except Exception as exc:
            validation_error = exc
        finally:
            if headquarters is not None:
                try:
                    headquarters.close()
                except Exception as close_exc:
                    raise _ValidationDatabaseOwnershipUncertain(
                        "post-update Headquarters could not prove canonical database release"
                    ) from close_exc
        if validation_error is not None:
            raise validation_error

    def apply(self, **kwargs) -> UpdateResult:  # type: ignore[override]
        plan = kwargs.get("plan")
        if "data_root" not in kwargs:
            raise TypeError("apply requires data_root")
        guarded_kwargs = dict(kwargs)
        guarded_kwargs["data_root"] = _canonical_data_root(kwargs["data_root"])
        try:
            return super().apply(**guarded_kwargs)
        except _ValidationDatabaseOwnershipUncertain as exc:
            if plan is None:
                raise
            journal = self._read_journal(plan.update_id)
            if journal.state == "VALIDATING":
                journal = self._mark_recovery(
                    plan,
                    expected={"VALIDATING"},
                    evidence="validation:database-release-uncertain",
                    reason=str(exc),
                    install_receipt=journal.install_receipt,
                )
            return self._to_result(journal)
