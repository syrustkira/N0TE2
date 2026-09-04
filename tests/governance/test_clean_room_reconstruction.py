import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CleanRoomReconstructionTests(unittest.TestCase):
    def load(self, rel):
        return json.loads((ROOT / rel).read_text())

    def test_canonical_scope_extensions_are_contiguous_and_non_selecting(self):
        requirements = self.load("governance/requirements.json")
        build_index = requirements["sequence"]
        canonical = requirements["canonical_scope"]

        self.assertEqual(build_index, {"start": 2, "end": 153})
        self.assertEqual(canonical["source"], "N0TE_PRODUCT_DB/SCOPE_LEDGER")
        self.assertEqual((canonical["start"], canonical["end"]), (2, 170))
        self.assertEqual(canonical["retained_requirement_count"], 169)

        extensions = requirements["canonical_extensions"]
        expected = [f"REQ-SCOPE-{n:03d}" for n in range(154, 171)]
        self.assertEqual([row["id"] for row in extensions], expected)
        for row in extensions:
            self.assertEqual(row["state"], "MAPPED")
            self.assertFalse(row["selected"])
            self.assertTrue(row["construction_affinity"])
            self.assertTrue(row["summary"].strip())

    def test_known_or_recovered_scope_cannot_select_work_by_itself(self):
        requirements = self.load("governance/requirements.json")
        self.assertFalse(requirements["known_selects_work"])
        self.assertIn("Work becomes ACTIVE only through current_state plus an active receipt", requirements["selection_contract"])
        self.assertTrue(all(row["selected"] is False for row in requirements["canonical_extensions"]))

    def test_stable_current_state_cannot_be_reactivated_by_historical_prose(self):
        current = self.load("governance/current_state.json")
        receipt = self.load("governance/active_receipt.json")

        self.assertEqual(current["lifecycle_state"], "STABLE")
        self.assertIsNone(current["active_node"])
        self.assertIsNone(current["active_increment"])
        self.assertFalse(current["product_code_authorized"])
        self.assertFalse(current["legacy_admission_authorized"])
        self.assertEqual(receipt["status"], "INACTIVE")

        # Truth/history text is evidence only. Command-like words inside prose do not
        # create an active build because activation is represented only by the
        # structured current-state + receipt contract above.
        historical_text = "CURRENT NEXT ACTIVE PRIORITY BUILD THIS NEXT"
        current["truth"].append(historical_text)
        self.assertIsNone(current["active_node"])
        self.assertIsNone(current["active_increment"])
        self.assertFalse(current["product_code_authorized"])

    def test_current_state_does_not_truncate_recovered_scope(self):
        current = self.load("governance/current_state.json")
        requirements = self.load("governance/requirements.json")
        canonical = requirements["canonical_scope"]
        summary = "\n".join(current["truth"])

        expected = (
            f"retains {canonical['retained_requirement_count']} requirements, "
            f"REQ-SCOPE-{canonical['start']:03d} through REQ-SCOPE-{canonical['end']:03d}"
        )
        self.assertIn(expected, summary)
        self.assertNotIn("retains 159 requirements, REQ-SCOPE-002 through REQ-SCOPE-160", summary)

    def test_superseded_history_is_preserved_without_reactivation(self):
        requirements = self.load("governance/requirements.json")
        graph = self.load("governance/completion_graph.json")
        mapped = "\n".join(str(node.get("requirements", "")) for node in graph["nodes"] if node["required"])

        self.assertIn("REQ-SCOPE-046", requirements["superseded"])
        self.assertNotIn("046", mapped)

    def test_new_professional_scope_is_retained_without_becoming_next_work(self):
        requirements = self.load("governance/requirements.json")
        current = self.load("governance/current_state.json")
        extensions = {row["id"]: row for row in requirements["canonical_extensions"]}

        for n in range(161, 171):
            row = extensions[f"REQ-SCOPE-{n:03d}"]
            self.assertFalse(row["selected"])
            self.assertEqual(row["state"], "MAPPED")

        self.assertEqual(current["lifecycle_state"], "STABLE")
        self.assertIsNone(current["active_node"])
        self.assertFalse(current["product_code_authorized"])


if __name__ == "__main__":
    unittest.main()
