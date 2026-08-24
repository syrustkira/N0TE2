from __future__ import annotations

from dataclasses import dataclass

from .lineage import LineageStore, Song, ValidationError, Version


class VersionApprovalError(RuntimeError):
    """An exact Song Version approval can no longer be applied safely."""


class StaleVersionApprovalError(VersionApprovalError):
    """The Song/current/approved state moved after approval authority was rendered."""


@dataclass(frozen=True)
class VersionApprovalBinding:
    song_id: str
    target_version_id: str
    expected_current_version_id: str | None
    expected_approved_version_id: str | None


@dataclass(frozen=True)
class VersionApprovalResult:
    song: Song
    approved_version: Version


class VersionApprovalService:
    """Apply one exact approval against the Song state the artist actually saw.

    This service owns no approval storage and no browser authority. It validates
    a rendered-state binding and delegates the actual canonical pointer mutation
    to LineageStore.approve_version, whose existing Activity hook records the
    VERSION_APPROVED chronology event.
    """

    def __init__(self, store: LineageStore):
        if not isinstance(store, LineageStore):
            raise TypeError("VersionApprovalService requires the canonical LineageStore")
        self.store = store

    def binding_for(self, song_id: str, target_version_id: str) -> VersionApprovalBinding:
        song = self.store._require_song(song_id)
        target = self.store.get_version(target_version_id)
        if target is None or target.song_id != song.id:
            raise ValidationError("approval target belongs to a different Song")
        return VersionApprovalBinding(
            song_id=song.id,
            target_version_id=target.id,
            expected_current_version_id=song.current_version_id,
            expected_approved_version_id=song.approved_version_id,
        )

    def approve(self, binding: VersionApprovalBinding) -> VersionApprovalResult:
        if not isinstance(binding, VersionApprovalBinding):
            raise TypeError("binding must be VersionApprovalBinding")
        active = self.store.active_song()
        if active is None or active.id != binding.song_id:
            raise StaleVersionApprovalError("active Song changed after approval was prepared")
        if active.current_version_id != binding.expected_current_version_id:
            raise StaleVersionApprovalError("current Version changed after approval was prepared")
        if active.approved_version_id != binding.expected_approved_version_id:
            raise StaleVersionApprovalError("approved Version changed after approval was prepared")
        target = self.store.get_version(binding.target_version_id)
        if target is None or target.song_id != active.id:
            raise VersionApprovalError("approval target is not a Version of the active Song")
        if active.approved_version_id == target.id:
            raise StaleVersionApprovalError("that exact Version is already approved")
        approved_song = self.store.approve_version(active.id, target.id)
        if approved_song.current_version_id != binding.expected_current_version_id:
            raise VersionApprovalError("approval unexpectedly changed the current Version")
        if approved_song.approved_version_id != target.id:
            raise VersionApprovalError("canonical approval pointer did not match the target Version")
        return VersionApprovalResult(song=approved_song, approved_version=target)
