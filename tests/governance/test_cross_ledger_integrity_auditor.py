import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "cross_ledger_integrity_auditor", ROOT / "governance/cross_ledger_integrity_auditor.py"
)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(audit)

NOW = datetime(2026, 9, 6, 1, 0, tzinfo=timezone.utc)


def valid_bundle():
    return {
        "source_versions": {
            "n0te_requirements": "n0te@abc",
            "public_scope": "public@def",
        },
        "reference_indexes": {
            "implementation_refs": ["impl-1"],
            "acceptance_refs": ["acc-ref-1"],
        },
        "n0te_requirements": {
            "retained_requirement_ids": ["REQ-SCOPE-001"],
            "rows": [
                {
                    "REQ_ID": "REQ-SCOPE-001",
                    "DISPOSITION": "RETAIN",
                    "RETAINED_SCOPE": "YES",
                }
            ],
        },
        "public_scope": {
            "retained_requirement_ids": ["PUB-REL-001"],
            "rows": [
                {
                    "REQ_ID": "PUB-REL-001",
                    "DISPOSITION": "RETAIN",
                    "RETAINED_SCOPE": "YES",
                }
            ],
        },
        "acceptance_evidence": {"rows": []},
        "action_receipts": {"rows": []},
        "public_product_bridge": {
            "rows": [
                {
                    "BRIDGE_ID": "B-1",
                    "PUB_REQUIREMENT_ID": "PUB-REL-001",
                    "N0TE_REQUIREMENT_ID": "REQ-SCOPE-001",
                    "RELATION_CLASS": "RELEASE_BRIDGE",
                    "IMPLEMENTATION_REF": "impl-1",
                    "ACCEPTANCE_REF": "acc-ref-1",
                }
            ]
        },
        "public_receipts": {
            "rows": [
                {
                    "RECEIPT_ID": "PR-1",
                    "consequential": True,
                    "TRACE_ID": "T-1",
                    "OPERATION_ID": "O-1",
                    "authority_basis": "Steward-authorized public read",
                    "state_basis": "fresh provider state",
                    "evidence_refs": ["provider-read-1"],
                    "acceptance_state": "PREPARED",
                }
            ]
        },
        "public_current_state": {
            "rows": [
                {
                    "id": "public-object-1",
                    "state": "LIVE",
                    "evidence_ref": "provider-read-1",
                    "evidence_checked_at": "2026-09-06T00:30:00Z",
                    "max_evidence_age_days": 1,
                }
            ]
        },
        "integration_receipts": {
            "rows": [
                {
                    "receipt_id": "IR-1",
                    "implementation_ref": "impl-1",
                    "n0te_requirement_ids": ["REQ-SCOPE-001"],
                    "public_affecting": True,
                    "public_handoff_ref": "PH-1",
                    "successor_public_state": "PUBLIC_PREPARED",
                    "integration_state": "MERGED_VERIFIED",
                    "public_acceptance_state": "PUBLIC_PREPARED",
                }
            ]
        },
        "equivalence_receipts": {"rows": []},
        "human_acceptance_receipts": {"rows": []},
    }


def finding_types(report, severity=None):
    rows = report["findings"]
    if severity:
        rows = [row for row in rows if row["severity"] == severity]
    return {row["type"] for row in rows}


class CrossLedgerIntegrityAuditorTests(unittest.TestCase):
    def test_omitted_retained_requirement_is_detected(self):
        bundle = valid_bundle()
        bundle["n0te_requirements"]["retained_requirement_ids"].append("REQ-SCOPE-002")
        report = audit.audit_bundle(bundle, now=NOW)
        self.assertIn("ORPHAN_REQUIREMENT", finding_types(report, "ERROR"))

    def test_dangling_bridge_is_detected(self):
        bundle = valid_bundle()
        bundle["public_product_bridge"]["rows"][0]["PUB_REQUIREMENT_ID"] = "PUB-REL-999"
        report = audit.audit_bundle(bundle, now=NOW)
        self.assertIn("DANGLING_BRIDGE", finding_types(report, "ERROR"))

    def test_missing_public_handoff_is_detected(self):
        bundle = valid_bundle()
        bundle["integration_receipts"]["rows"][0].pop("public_handoff_ref")
        report = audit.audit_bundle(bundle, now=NOW)
        self.assertIn("ORPHAN_PUBLIC_HANDOFF", finding_types(report, "ERROR"))

    def test_merge_does_not_prove_public_acceptance(self):
        bundle = valid_bundle()
        bundle["integration_receipts"]["rows"][0]["public_acceptance_state"] = "PUBLIC_VERIFIED"
        report = audit.audit_bundle(bundle, now=NOW)
        self.assertIn("AUTHORITY_MISMATCH", finding_types(report, "ERROR"))

    def test_historical_trace_gap_warns_without_fabrication(self):
        bundle = valid_bundle()
        bundle["public_receipts"]["rows"] = [
            {
                "RECEIPT_ID": "HIST-1",
                "historical": True,
                "consequential": True,
                "evidence_ref": "legacy-evidence",
            }
        ]
        report = audit.audit_bundle(bundle, now=NOW)
        warnings = [f for f in report["findings"] if f["severity"] == "WARN"]
        self.assertTrue(any(f["type"] == "ORPHAN_PUBLIC_RECEIPT" for f in warnings))
        self.assertNotIn("TRACE_ID", bundle["public_receipts"]["rows"][0])

    def test_incomplete_equivalence_fails(self):
        bundle = valid_bundle()
        bundle["n0te_requirements"]["rows"][0]["DISPOSITION"] = "SUPERSEDED_BY_EQUIVALENT"
        bundle["n0te_requirements"]["rows"][0]["RETAINED_SCOPE"] = "NO"
        bundle["n0te_requirements"]["rows"][0]["SUPERSEDED_BY"] = "REQ-SCOPE-002"
        bundle["equivalence_receipts"]["rows"] = [
            {
                "EQUIVALENCE_ID": "EQ-1",
                "original_requirement": "REQ-SCOPE-001",
                "replacement_requirement": "REQ-SCOPE-002",
            }
        ]
        report = audit.audit_bundle(bundle, now=NOW)
        self.assertIn("UNPROVEN_EQUIVALENCE", finding_types(report, "ERROR"))

    def test_waiting_without_wake_condition_is_detected(self):
        bundle = valid_bundle()
        bundle["public_current_state"]["rows"].append(
            {
                "id": "WAIT-1",
                "state": "WAITING",
                "blocker": "provider",
                "owner": "Public Executor",
                "preserved_valid_work": "source asset",
                "required_proof": "provider receipt",
            }
        )
        report = audit.audit_bundle(bundle, now=NOW)
        self.assertIn("MISSING_RESURRECTION_CONDITION", finding_types(report, "ERROR"))

    def test_valid_fully_linked_sample_passes(self):
        report = audit.audit_bundle(valid_bundle(), now=NOW)
        self.assertEqual(report["finding_counts"]["ERROR"], 0)
        self.assertEqual(report["finding_counts"]["WARN"], 0)
        self.assertTrue(report["no_automatic_mutation"])

    def test_auditor_cannot_alter_semantic_source_files(self):
        bundle = valid_bundle()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            snapshots = root / "snapshots"
            output = root / "reports"
            snapshots.mkdir()
            for key, filename in audit.INPUT_FILES.items():
                (snapshots / filename).write_text(
                    json.dumps(bundle[key], indent=2) + "\n", encoding="utf-8"
                )
            (snapshots / "source_versions.json").write_text(
                json.dumps(bundle["source_versions"], indent=2) + "\n", encoding="utf-8"
            )
            before = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in snapshots.iterdir() if path.is_file()
            }
            report = audit.audit_snapshot_dir(snapshots, output, now=NOW)
            after = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in snapshots.iterdir() if path.is_file()
            }
            self.assertEqual(before, after)
            self.assertTrue((output / audit.OUTPUT_JSON).is_file())
            self.assertTrue((output / audit.OUTPUT_MD).is_file())
            self.assertEqual(report["finding_counts"]["ERROR"], 0)


if __name__ == "__main__":
    unittest.main()
