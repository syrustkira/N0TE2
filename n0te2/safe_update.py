from __future__ import annotations

from pathlib import Path

from .memory import HeadquartersMemory
from .update import (
    ApplicationUpdateCoordinator as _BaseApplicationUpdateCoordinator,
    UpdateRejectedError,
    UpdateResult,
)


class _ValidationDatabaseOwnershipUncertain(BaseException):
    """Bypass automatic compensation when the validation database may still be open."""


class ApplicationUpdateCoordinator(_BaseApplicationUpdateCoordinator):
    """Consumer-safe APP-01C update coordinator.

    The durable transaction/state machine lives in ``n0te2.update``. This thin
    guard changes only the post-install validation-close failure class: package
    rollback and creative-state restore are forbidden unless the validation
    Headquarters proves it released the canonical database first.
    """

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
        try:
            return super().apply(**kwargs)
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
