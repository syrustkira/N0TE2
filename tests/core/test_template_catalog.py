from __future__ import annotations

import sqlite3

import pytest

from n0te2.lineage import LineageCorruptionError, LineageStore
from n0te2.template_catalog import TemplateCatalog, TemplateCatalogError
from n0te2.templates import TemplateDefinition, TemplateRole


def _template(template_id: str = "template:test:vocal") -> TemplateDefinition:
    return TemplateDefinition(
        template_id=template_id,
        family="VOCAL",
        name="Vocal Start",
        intent="Begin vocal work from semantic roles rather than a host track layout",
        roles=(
            TemplateRole(
                role_id="lead-edit",
                capability="vocal.tighten",
                description="Tighten the lead while preserving performance intent",
                required=True,
                tags=("editing", "lead"),
            ),
            TemplateRole(
                role_id="harmony",
                capability="vocal.harmony.build",
                description="Build optional supporting harmonies",
                required=False,
                tags=("harmony",),
            ),
        ),
    )


def test_catalog_reads_do_not_initialize_schema(tmp_path) -> None:
    store = LineageStore.create(tmp_path, "Pure Read Artist")
    try:
        song = store.create_song("Pure Read Song")
        catalog = TemplateCatalog(store)
        assert catalog.templates() == ()
        assert catalog.current_selection(song.id) is None
        assert (
            store._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='template_definitions'"
            ).fetchone()
            is None
        )
        assert (
            store._conn.execute(
                "SELECT value FROM metadata WHERE key='template_catalog_schema_version'"
            ).fetchone()
            is None
        )
    finally:
        store.close()


def test_multi_role_template_round_trips_and_selection_is_append_only(tmp_path) -> None:
    store = LineageStore.create(tmp_path, "Template Artist")
    song = store.create_song("Template Song")
    catalog = TemplateCatalog(store)
    first = catalog.save(_template())
    second = catalog.create(
        family="MIX",
        name="Mix Start",
        intent="Prepare a reversible technical mix starting point",
        roles=(
            TemplateRole(
                role_id="repair",
                capability="audio.repair",
                description="Repair obvious technical defects",
                required=True,
            ),
        ),
    )
    first_selection = catalog.select_for_song(
        song_id=song.id, template_id=first.template_id
    )
    second_selection = catalog.select_for_song(
        song_id=song.id, template_id=second.template_id
    )
    assert first_selection.source_kind == "ARTIST_DECLARED"
    assert second_selection.sequence > first_selection.sequence
    assert tuple(item.template_id for item in catalog.selection_history(song.id)) == (
        first.template_id,
        second.template_id,
    )
    assert catalog.selected_template(song.id) == second
    store.close()

    reopened = LineageStore.open(tmp_path, store.profile_id)
    try:
        catalog = TemplateCatalog(reopened)
        assert catalog.templates() == (first, second)
        assert catalog.selected_template(song.id) == second
        restored = catalog.get(first.template_id)
        assert restored == first
        assert restored is not None
        assert restored.roles[0].role_id == "harmony"
        assert restored.roles[1].role_id == "lead-edit"
    finally:
        reopened.close()


def test_template_identity_cannot_be_rewritten_or_deleted(tmp_path) -> None:
    store = LineageStore.create(tmp_path, "Immutable Artist")
    try:
        song = store.create_song("Immutable Song")
        catalog = TemplateCatalog(store)
        definition = catalog.save(_template())
        selection = catalog.select_for_song(
            song_id=song.id, template_id=definition.template_id
        )

        with pytest.raises(sqlite3.DatabaseError):
            store._conn.execute(
                "UPDATE template_definitions SET name='Rewritten' WHERE id=?",
                (definition.template_id,),
            )
        with pytest.raises(sqlite3.DatabaseError):
            store._conn.execute(
                "DELETE FROM template_definitions WHERE id=?",
                (definition.template_id,),
            )
        with pytest.raises(sqlite3.DatabaseError):
            store._conn.execute(
                "UPDATE template_selections SET source_kind='ARTIST_DECLARED' WHERE id=?",
                (selection.id,),
            )
        with pytest.raises(sqlite3.DatabaseError):
            store._conn.execute(
                "DELETE FROM template_selections WHERE id=?",
                (selection.id,),
            )

        assert catalog.get(definition.template_id) == definition
        assert catalog.current_selection(song.id) == selection
    finally:
        store.close()


def test_same_identity_with_different_meaning_is_rejected(tmp_path) -> None:
    store = LineageStore.create(tmp_path, "Identity Artist")
    try:
        store.create_song("Identity Song")
        catalog = TemplateCatalog(store)
        original = catalog.save(_template("stable-template"))
        assert catalog.save(original) == original
        conflicting = TemplateDefinition(
            template_id=original.template_id,
            family="VOCAL",
            name="Different meaning",
            intent=original.intent,
            roles=original.roles,
        )
        with pytest.raises(TemplateCatalogError):
            catalog.save(conflicting)
    finally:
        store.close()


def test_missing_integrity_trigger_fails_closed_on_reopen(tmp_path) -> None:
    store = LineageStore.create(tmp_path, "Corruption Artist")
    profile_id = store.profile_id
    try:
        store.create_song("Corruption Song")
        TemplateCatalog(store).save(_template())
        store._conn.execute("DROP TRIGGER template_definitions_immutable_update")
        store._conn.commit()
    finally:
        store.close()

    reopened = LineageStore.open(tmp_path, profile_id)
    try:
        with pytest.raises(LineageCorruptionError):
            TemplateCatalog(reopened)
    finally:
        reopened.close()


def test_catalog_bounds_semantic_roles_without_host_fields(tmp_path) -> None:
    store = LineageStore.create(tmp_path, "Bounds Artist")
    try:
        store.create_song("Bounds Song")
        catalog = TemplateCatalog(store)
        too_many = tuple(
            TemplateRole(
                role_id=f"r{index}",
                capability=f"cap.{index}",
                description=f"Role {index}",
            )
            for index in range(9)
        )
        definition = TemplateDefinition(
            template_id="too-many",
            family="SONG",
            name="Too many",
            intent="Bound the reusable start",
            roles=too_many,
        )
        with pytest.raises(TemplateCatalogError):
            catalog.save(definition)

        assert "host" not in TemplateDefinition.__dataclass_fields__
        assert "provider" not in TemplateDefinition.__dataclass_fields__
        assert "track" not in TemplateDefinition.__dataclass_fields__
        assert "host" not in TemplateRole.__dataclass_fields__
        assert "provider" not in TemplateRole.__dataclass_fields__
        assert "track" not in TemplateRole.__dataclass_fields__
    finally:
        store.close()
