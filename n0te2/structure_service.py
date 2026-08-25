from __future__ import annotations

from dataclasses import dataclass

from .lineage import ValidationError
from .structure import (
    SongSection,
    SongStructureMemory,
    SongStructureRevision,
    StaleSongStructureError,
)


@dataclass(frozen=True)
class StructureBinding:
    song_id: str
    expected_revision_id: str | None


@dataclass(frozen=True)
class StructureSectionBinding:
    song_id: str
    expected_revision_id: str
    section_index: int


class SongStructureService:
    """Artist-facing stale-safe operations over canonical revisioned Structure memory."""

    def __init__(self, structure: SongStructureMemory):
        if not isinstance(structure, SongStructureMemory):
            raise TypeError("SongStructureService requires canonical SongStructureMemory")
        self.structure = structure
        self.store = structure.store

    def binding_for_active_song(self) -> StructureBinding | None:
        song = self.store.active_song()
        if song is None:
            return None
        current = self.structure.current(song.id)
        return StructureBinding(song.id, None if current is None else current.id)

    def section_binding(self, index: int) -> StructureSectionBinding:
        song = self.store.active_song()
        if song is None:
            raise StaleSongStructureError("No active Song is available for Structure editing.")
        current = self.structure.current(song.id)
        if current is None:
            raise StaleSongStructureError("The Song has no Structure revision to edit.")
        if isinstance(index, bool) or not isinstance(index, int) or not (0 <= index < len(current.sections)):
            raise ValidationError("section index is invalid")
        return StructureSectionBinding(song.id, current.id, index)

    @staticmethod
    def _section(label: str, first_bar: str | int, last_bar: str | int, note: str | None) -> SongSection:
        return SongStructureMemory.section(label, first_bar, last_bar, note)

    def _current_for_binding(self, binding: StructureBinding) -> SongStructureRevision | None:
        song = self.store.active_song()
        if song is None or song.id != binding.song_id:
            raise StaleSongStructureError(
                "The active Song changed after this Structure action was prepared."
            )
        current = self.structure.current(binding.song_id)
        current_id = None if current is None else current.id
        if current_id != binding.expected_revision_id:
            raise StaleSongStructureError(
                "The Song Structure changed after this page was prepared."
            )
        return current

    def add_section(
        self,
        binding: StructureBinding,
        *,
        label: str,
        first_bar: str | int,
        last_bar: str | int,
        note: str | None = None,
    ) -> SongStructureRevision:
        if not isinstance(binding, StructureBinding):
            raise TypeError("binding must be StructureBinding")
        current = self._current_for_binding(binding)
        sections = () if current is None else current.sections
        candidate = self._section(label, first_bar, last_bar, note)
        return self.structure.commit(
            song_id=binding.song_id,
            sections=sections + (candidate,),
            expected_revision_id=binding.expected_revision_id,
            change_kind="ADD_SECTION",
            require_active_song=True,
        )

    def _section_current(self, binding: StructureSectionBinding) -> SongStructureRevision:
        if not isinstance(binding, StructureSectionBinding):
            raise TypeError("binding must be StructureSectionBinding")
        current = self._current_for_binding(
            StructureBinding(binding.song_id, binding.expected_revision_id)
        )
        if current is None or not (0 <= binding.section_index < len(current.sections)):
            raise StaleSongStructureError("That Structure section changed or disappeared.")
        return current

    def edit_section(
        self,
        binding: StructureSectionBinding,
        *,
        label: str,
        first_bar: str | int,
        last_bar: str | int,
        note: str | None = None,
    ) -> SongStructureRevision:
        current = self._section_current(binding)
        replacement = self._section(label, first_bar, last_bar, note)
        sections = list(current.sections)
        sections[binding.section_index] = replacement
        return self.structure.commit(
            song_id=binding.song_id,
            sections=tuple(sections),
            expected_revision_id=binding.expected_revision_id,
            change_kind="EDIT_SECTION",
            require_active_song=True,
        )

    def remove_section(self, binding: StructureSectionBinding) -> SongStructureRevision:
        current = self._section_current(binding)
        sections = list(current.sections)
        sections.pop(binding.section_index)
        return self.structure.commit(
            song_id=binding.song_id,
            sections=tuple(sections),
            expected_revision_id=binding.expected_revision_id,
            change_kind="REMOVE_SECTION",
            require_active_song=True,
        )

    def undo_last(self, binding: StructureBinding) -> SongStructureRevision:
        if not isinstance(binding, StructureBinding):
            raise TypeError("binding must be StructureBinding")
        current = self._current_for_binding(binding)
        if current is None or current.parent_revision_id is None:
            raise ValidationError("There is no earlier Song Structure revision to restore.")
        parent = self.structure.get_revision(current.parent_revision_id)
        if parent is None or parent.song_id != binding.song_id:
            raise StaleSongStructureError("The prior Song Structure revision is unavailable.")
        return self.structure.commit(
            song_id=binding.song_id,
            sections=parent.sections,
            expected_revision_id=binding.expected_revision_id,
            change_kind="RESTORE_PREVIOUS",
            require_active_song=True,
        )
