from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "n0te2_governance_incident_event_history",
    ROOT / "governance/check_governance.py",
)
gov = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gov)


class IncidentEventHistoryTests(unittest.TestCase):
    def clone(self) -> Path:
        td = tempfile.TemporaryDirectory()
        dst = Path(td.name) / "repo"
        shutil.copytree(
            ROOT,
            dst,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        self.addCleanup(td.cleanup)
        return dst

    def test_incident_history_allows_later_event_with_same_incident_id(self) -> None:
        repo = self.clone()
        path = repo / "governance/incidents.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        source = next(row for row in rows if row.get("id") == "INC-2026-09-05-STEWARD-INTEGRATION-206")
        rows.append(
            {
                "id": source["id"],
                "recorded_at": "2026-09-05",
                "status": "RESOLVED_TEST_FIXTURE",
                "severity": "TEST_ONLY",
                "summary": "Append-only resolution event for duplicate-ID policy regression.",
            }
        )
        path.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n")
        parsed = gov.check_jsonl_ids(repo, "governance/incidents.jsonl", allow_repeated_ids=True)
        self.assertEqual(sum(row.get("id") == source["id"] for row in parsed), 2)

    def test_non_incident_registry_still_rejects_duplicate_stable_id(self) -> None:
        repo = self.clone()
        path = repo / "governance/decisions.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        rows.append(dict(rows[0]))
        path.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n")
        with self.assertRaises(gov.GovernanceError) as cm:
            gov.check_jsonl_ids(repo, "governance/decisions.jsonl")
        self.assertIn("duplicate stable ids", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
