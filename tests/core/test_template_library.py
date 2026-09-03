import sqlite3
import tempfile
import unittest

from n0te2.lineage import LineageCorruptionError, ValidationError
from n0te2.memory import HeadquartersMemory
from n0te2.templates import TemplateDefinition, TemplateRole


class TemplateLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hq = HeadquartersMemory.create(self.tmp.name, "Template Artist")
        self.song = self.hq.store.create_song("Template Song")

    def tearDown(self):
        try:
            self.hq.close()
        except sqlite3.ProgrammingError:
            pass

    @staticmethod
    def template(template_id="template:test:vocal", *, name="Vocal Start"):
        return TemplateDefinition(
            template_id=template_id,
            family="VOCAL",
            name=name,
            intent="Begin vocal production without binding the start to a host",
            roles=(
                TemplateRole(
                    role_id="primary",
                    capability="vocal.tighten",
                    description="Tighten the lead while preserving performance intent",
                    required=True,
                ),
            ),
        )

    def test_definition_round_trips_exactly_and_survives_relaunch(self):
        template = self.template()
        self.assertEqual(self.hq.template_library.save(template), template)
        self.assertEqual(self.hq.template_library.get(template.template_id), template)
        self.assertEqual(self.hq.template_library.all(), (template,))

        profile_id = self.hq.store.profile_id
        self.hq.close()
        self.hq = HeadquartersMemory.open(self.tmp.name, profile_id)
        self.assertEqual(self.hq.template_library.get(template.template_id), template)
        self.assertEqual(self.hq.template_library.all(), (template,))

    def test_identical_save_is_idempotent_but_conflicting_identity_fails_closed(self):
        template = self.template()
        before = len(self.hq.activity.for_profile())
        first = self.hq.template_library.save(template)
        second = self.hq.template_library.save(template)
        self.assertEqual(first, second)
        events = self.hq.activity.for_profile()[before:]
        self.assertEqual([event.event_type for event in events], ["TEMPLATE_SAVED"])

        with self.assertRaisesRegex(ValidationError, "different immutable definition"):
            self.hq.template_library.save(self.template(name="Different Meaning"))

    def test_song_selection_is_durable_append_only_and_song_scoped(self):
        first = self.hq.template_library.save(self.template())
        second = self.hq.template_library.save(
            TemplateDefinition(
                template_id="template:test:mix",
                family="MIX",
                name="Mix Start",
                intent="Prepare a reversible mix starting point",
                roles=(TemplateRole("primary", "audio.repair", "Repair technical defects"),),
            )
        )
        selected_first = self.hq.template_library.select_for_song(self.song.id, first.template_id)
        self.assertEqual(
            self.hq.template_library.select_for_song(self.song.id, first.template_id),
            selected_first,
        )
        selected_second = self.hq.template_library.select_for_song(self.song.id, second.template_id)
        self.assertNotEqual(selected_first.id, selected_second.id)
        self.assertEqual(
            [item.template_id for item in self.hq.template_library.selection_history(self.song.id)],
            [first.template_id, second.template_id],
        )
        self.assertEqual(
            self.hq.template_library.selected_for_song(self.song.id).template_id,
            second.template_id,
        )

        other = self.hq.store.create_song("Other Song")
        self.assertIsNone(self.hq.template_library.selected_for_song(other.id))
        self.hq.template_library.select_for_song(other.id, first.template_id)
        self.assertEqual(
            self.hq.template_library.selected_for_song(other.id).template_id,
            first.template_id,
        )
        self.assertEqual(
            self.hq.template_library.selected_for_song(self.song.id).template_id,
            second.template_id,
        )

    def test_definition_and_selection_rows_are_immutable(self):
        template = self.hq.template_library.save(self.template())
        selection = self.hq.template_library.select_for_song(self.song.id, template.template_id)
        conn = self.hq.store._conn
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE template_definitions SET name='rewritten' WHERE template_id=?",
                (template.template_id,),
            )
        conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM template_roles WHERE template_id=?", (template.template_id,))
        conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM song_template_selections WHERE id=?", (selection.id,))
        conn.rollback()

    def test_save_and_select_do_not_create_execution_operations(self):
        template = self.hq.template_library.save(self.template())
        self.hq.template_library.select_for_song(self.song.id, template.template_id)
        count = int(self.hq.store._conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0])
        self.assertEqual(count, 0)
        events = self.hq.activity.for_song(self.song.id)
        self.assertEqual(events[-1].event_type, "TEMPLATE_SELECTED")

    def test_profile_isolation_is_physical_and_semantic(self):
        template = self.hq.template_library.save(self.template())
        other = HeadquartersMemory.create(self.tmp.name, "Other Artist")
        self.addCleanup(other.close)
        self.assertIsNone(other.template_library.get(template.template_id))
        self.assertEqual(other.template_library.all(), ())

    def test_missing_template_library_metadata_fails_visibly_on_reopen(self):
        self.hq.template_library.save(self.template())
        profile_id = self.hq.store.profile_id
        self.hq.store._conn.execute(
            "DELETE FROM metadata WHERE key='template_library_schema_version'"
        )
        self.hq.store._conn.commit()
        self.hq.close()
        with self.assertRaisesRegex(LineageCorruptionError, "Template library schema"):
            HeadquartersMemory.open(self.tmp.name, profile_id)


if __name__ == "__main__":
    unittest.main()
