#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SEVERITIES = ("ERROR", "WARN", "INFO")
FINDING_TYPES = {
    "ORPHAN_REQUIREMENT",
    "ORPHAN_IMPLEMENTATION",
    "ORPHAN_PUBLIC_HANDOFF",
    "ORPHAN_PUBLIC_RECEIPT",
    "ORPHAN_ACCEPTANCE",
    "DANGLING_BRIDGE",
    "UNPROVEN_EQUIVALENCE",
    "STALE_LIVE_CLAIM",
    "MISSING_RESURRECTION_CONDITION",
    "AUTHORITY_MISMATCH",
}
RELATION_CLASSES = {
    "IMPLEMENTS_PUBLIC_JOB",
    "ENABLES_PUBLIC_JOB",
    "PROVIDES_EVIDENCE_FOR",
    "CONSUMES_PUBLIC_STATE",
    "PUBLIC_ACCEPTANCE_OF",
    "RIGHTS_BRIDGE",
    "FAN_BRIDGE",
    "RELEASE_BRIDGE",
    "PROFESSIONAL_BRIDGE",
    "NO_PUBLIC_MAPPING_REQUIRED",
}
WAITING_STATES = {
    "WAITING",
    "FIX",
    "FIX_REQUIRED",
    "REBUILD",
    "REBUILD_REQUIRED",
    "SPLIT",
    "PROVIDER_DEPENDENT",
    "HUMAN_DEPENDENT",
}
PUBLIC_TRUTH_STATES = {"LIVE", "VERIFIED", "PUBLIC_VERIFIED"}
COMPLETED_STATES = {"COMPLETE", "COMPLETED", "VERIFIED", "LIVE", "PUBLIC_VERIFIED", "ACCEPTED"}
REQ_RE = re.compile(r"^REQ-SCOPE-\d{3,}$")
PUB_RE = re.compile(r"^PUB-[A-Z0-9]+-\d{3,}$")

INPUT_FILES = {
    "n0te_requirements": "n0te_requirements.json",
    "acceptance_evidence": "acceptance_evidence.json",
    "action_receipts": "action_receipts.json",
    "public_scope": "public_scope.json",
    "public_product_bridge": "public_product_bridge.json",
    "public_receipts": "public_receipts.json",
    "public_current_state": "public_current_state.json",
    "integration_receipts": "integration_receipts.json",
    "equivalence_receipts": "equivalence_receipts.json",
    "human_acceptance_receipts": "human_acceptance_receipts.json",
}
OUTPUT_JSON = "cross_ledger_integrity_report.json"
OUTPUT_MD = "CROSS_LEDGER_INTEGRITY_REPORT.md"


class AuditInputError(RuntimeError):
    pass


def _value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
        upper = name.upper()
        if upper in row:
            return row[upper]
        lower = name.lower()
        if lower in row:
            return row[lower]
    return None


def _rows(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if isinstance(value, dict):
        for key in ("rows", "requirements", "receipts", "items", "records", "state", "bridges"):
            rows = value.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
        if any(k in value for k in ("id", "REQ_ID", "receipt_id", "RECEIPT_ID", "BRIDGE_ID")):
            return [dict(value)]
    return []


def _id_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_id_list(item))
        return out
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[;,]", value) if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    return str(value or "").strip().upper() in {"TRUE", "YES", "Y", "1", "RETAIN", "RETAINED", "ACTIVE"}


def _state(row: Mapping[str, Any]) -> str:
    return str(_value(row, "state", "current_state", "status", "disposition", "current_disposition", "decision") or "").strip().upper()


def _row_id(row: Mapping[str, Any]) -> str:
    return str(_value(
        row,
        "REQ_ID", "requirement_id", "id", "BRIDGE_ID", "bridge_id", "RECEIPT_ID",
        "receipt_id", "ACCEPTANCE_ID", "acceptance_id", "EQUIVALENCE_ID", "equivalence_id",
        "implementation_id", "operation_id",
    ) or "").strip()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _finding(
    findings: list[dict[str, Any]],
    finding_type: str,
    severity: str,
    message: str,
    affected_ids: Iterable[str] = (),
    suggested_owner: str = "Main Steward",
    evidence: Mapping[str, Any] | None = None,
) -> None:
    if finding_type not in FINDING_TYPES:
        raise AuditInputError(f"unknown finding type: {finding_type}")
    if severity not in SEVERITIES:
        raise AuditInputError(f"unknown severity: {severity}")
    ids = sorted({str(v) for v in affected_ids if str(v)})
    findings.append(
        {
            "finding_id": f"F-{len(findings)+1:04d}",
            "type": finding_type,
            "severity": severity,
            "affected_ids": ids,
            "suggested_owner": suggested_owner,
            "message": message,
            "evidence": dict(evidence or {}),
        }
    )


def _projection_ids(projection: Any, *, public: bool = False) -> set[str]:
    pattern = PUB_RE if public else REQ_RE
    return {
        rid for rid in (_row_id(row) for row in _rows(projection))
        if pattern.fullmatch(rid)
    }


def _expected_retained_ids(projection: Any) -> set[str]:
    if not isinstance(projection, dict):
        return set()
    return set(_id_list(projection.get("retained_requirement_ids")))


def _explicit_retained(row: Mapping[str, Any]) -> bool:
    retained = _value(row, "retained", "retained_scope", "RETAINED_SCOPE")
    if retained is not None:
        return _truthy(retained)
    disp = str(_value(row, "disposition", "current_disposition") or "").strip().upper()
    return disp in {
        "RETAIN", "RETAINED", "ACTIVE", "WAITING", "FIX", "FIX_REQUIRED", "REBUILD",
        "REBUILD_REQUIRED", "SPLIT", "PROVIDER_DEPENDENT", "HUMAN_DEPENDENT",
    }


def _check_requirement_projection(
    findings: list[dict[str, Any]],
    projection: Any,
    *,
    public: bool,
    owner: str,
) -> None:
    pattern = PUB_RE if public else REQ_RE
    label = "public" if public else "N0TE"
    rows = _rows(projection)
    ids = {_row_id(row) for row in rows if _row_id(row)}
    expected = _expected_retained_ids(projection)

    for missing in sorted(expected - ids):
        _finding(
            findings, "ORPHAN_REQUIREMENT", "ERROR",
            f"Retained {label} requirement disappeared from the current materialized projection.",
            [missing], owner,
        )

    for row in rows:
        rid = _row_id(row)
        if not rid:
            continue
        if not pattern.fullmatch(rid):
            _finding(
                findings, "ORPHAN_REQUIREMENT", "ERROR",
                f"{label} canonical requirement id is malformed.", [rid], owner,
            )
            continue
        if _explicit_retained(row):
            disposition = str(_value(row, "disposition", "current_disposition", "status") or "").strip()
            if not disposition:
                _finding(
                    findings, "ORPHAN_REQUIREMENT", "ERROR",
                    f"Retained {label} requirement has no current disposition.", [rid], owner,
                )
        disp = str(_value(row, "disposition", "current_disposition") or "").strip().upper()
        if disp.startswith("SUPERSEDED"):
            successor = _value(row, "successor", "superseded_by", "current_successor")
            if not successor:
                _finding(
                    findings, "ORPHAN_REQUIREMENT", "ERROR",
                    f"Superseded {label} requirement has no represented successor.", [rid], owner,
                )


def _known_refs(bundle: Mapping[str, Any], key: str) -> set[str] | None:
    meta = bundle.get("reference_indexes")
    if not isinstance(meta, dict) or key not in meta:
        return None
    return set(_id_list(meta.get(key)))


def _check_bridges(
    findings: list[dict[str, Any]],
    bundle: Mapping[str, Any],
    n0te_ids: set[str],
    pub_ids: set[str],
) -> None:
    impl_index = _known_refs(bundle, "implementation_refs")
    acceptance_index = _known_refs(bundle, "acceptance_refs")
    for row in _rows(bundle.get("public_product_bridge")):
        bid = _row_id(row) or "<bridge-without-id>"
        pub = str(_value(row, "PUB_REQUIREMENT_ID", "pub_requirement_id") or "").strip()
        n0te = str(_value(row, "N0TE_REQUIREMENT_ID", "n0te_requirement_id") or "").strip()
        relation = str(_value(row, "RELATION_CLASS", "relation_class") or "").strip().upper()
        if pub not in pub_ids:
            _finding(
                findings, "DANGLING_BRIDGE", "ERROR",
                "Public product bridge references a missing/invalid public requirement.",
                [bid, pub], "Public Scope Steward",
            )
        if n0te not in n0te_ids:
            _finding(
                findings, "DANGLING_BRIDGE", "ERROR",
                "Public product bridge references a missing/invalid N0TE requirement.",
                [bid, n0te], "Main Steward",
            )
        if relation not in RELATION_CLASSES:
            _finding(
                findings, "DANGLING_BRIDGE", "ERROR",
                "Public product bridge relation class is outside the canonical allowed set.",
                [bid], "Main Steward", {"relation_class": relation},
            )
        impl = str(_value(row, "IMPLEMENTATION_REF", "implementation_ref") or "").strip()
        if impl and impl_index is not None and impl not in impl_index:
            _finding(
                findings, "DANGLING_BRIDGE", "ERROR",
                "Bridge implementation reference does not resolve in the supplied reference index.",
                [bid, impl], "Builder",
            )
        acc = str(_value(row, "ACCEPTANCE_REF", "acceptance_ref") or "").strip()
        if acc and acceptance_index is not None and acc not in acceptance_index:
            _finding(
                findings, "DANGLING_BRIDGE", "ERROR",
                "Bridge acceptance reference does not resolve in the supplied reference index.",
                [bid, acc], "Main Steward",
            )


def _public_affecting(row: Mapping[str, Any]) -> bool:
    flag = _value(row, "public_affecting", "public_consequence", "public_handoff_required")
    if flag is not None:
        return _truthy(flag)
    for field in ("public_domains_affected", "public_assets_affected", "providers_affected"):
        if _id_list(_value(row, field)):
            return True
    return False


def _check_integrations(findings: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    for row in rows:
        iid = _row_id(row) or str(_value(row, "pr", "PR") or "<integration>")
        reqs = _id_list(_value(row, "n0te_requirement_ids", "engineering_requirement_ids"))
        if not reqs and _value(row, "implementation_ref", "implementation_id"):
            _finding(
                findings, "ORPHAN_IMPLEMENTATION", "ERROR",
                "Implementation/integration receipt is not attributed to any engineering requirement.",
                [iid], "Builder",
            )
        if not _public_affecting(row):
            continue
        handoff = _value(row, "public_handoff_ref", "public_deployment_handoff", "builder_handoff")
        successor = _value(row, "successor_public_state", "public_successor_state")
        if not handoff:
            _finding(
                findings, "ORPHAN_PUBLIC_HANDOFF", "ERROR",
                "Public-affecting merged implementation has no public deployment handoff.",
                [iid], "Main Steward",
            )
        if not successor:
            _finding(
                findings, "ORPHAN_PUBLIC_HANDOFF", "ERROR",
                "Public-affecting merged implementation has no successor public state.",
                [iid], "Main Steward",
            )
        public_acceptance = str(_value(row, "public_acceptance_state", "public_state") or "").strip().upper()
        merge_state = str(_value(row, "integration_state", "merge_state", "result_state") or "").strip().upper()
        if public_acceptance == "PUBLIC_VERIFIED" and merge_state in {"MERGED", "MERGED_VERIFIED", "SUCCEEDED", "VERIFIED"}:
            _finding(
                findings, "AUTHORITY_MISMATCH", "ERROR",
                "Repository merge evidence was used to claim PUBLIC_VERIFIED; merge and public acceptance must remain distinct.",
                [iid], "Main Steward",
            )


def _is_consequential_receipt(row: Mapping[str, Any]) -> bool:
    explicit = _value(row, "consequential", "current_operation", "current")
    if explicit is not None:
        return _truthy(explicit)
    return _state(row) in PUBLIC_TRUTH_STATES | COMPLETED_STATES


def _check_public_receipts(findings: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    required = (
        ("trace_id", "trace id"),
        ("operation_id", "operation id"),
        ("authority_basis", "authority basis"),
        ("state_basis", "state basis"),
    )
    for row in rows:
        if not _is_consequential_receipt(row):
            continue
        rid = _row_id(row) or "<public-receipt>"
        historical = _truthy(_value(row, "historical", "legacy_record"))
        missing: list[str] = []
        for field, label in required:
            if not _value(row, field):
                missing.append(label)
        if not _value(row, "execution_evidence", "evidence_refs", "evidence_ref", "LINK_OR_REF"):
            missing.append("execution evidence")
        accepted = str(_value(row, "acceptance_state", "decision", "result") or "").strip().upper() in COMPLETED_STATES
        if accepted and not _value(row, "acceptance_ref", "acceptance_refs"):
            missing.append("acceptance link")
        if missing:
            _finding(
                findings, "ORPHAN_PUBLIC_RECEIPT", "WARN" if historical else "ERROR",
                ("Historical receipt has an explicit legacy trace gap; do not fabricate missing identifiers."
                 if historical else "Current consequential public operation has an incomplete trace chain.")
                + " Missing: " + ", ".join(missing),
                [rid], "Main Steward",
            )


EQUIVALENCE_FIELDS = (
    ("original_requirement", "original_requirement_id"),
    ("replacement_requirement", "replacement_requirement_id", "replacement_implementation"),
    ("retained_semantic_coverage", "exact_retained_semantic_coverage", "semantic_coverage_mapping"),
    ("intentionally_changed_semantics", "changed_semantics", "changed_behavior"),
    ("uncovered_residue",),
    ("affected_dependencies",),
    ("acceptance_evidence", "acceptance_evidence_ref"),
    ("authority_permitting_equivalence", "semantic_authority"),
    ("historical_lineage", "lineage"),
    ("current_successor", "successor_status"),
    ("trace_id",),
)


def _equivalence_original(row: Mapping[str, Any]) -> str:
    return str(_value(row, "original_requirement", "original_requirement_id") or "").strip()


def _residue_is_none(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return str(value).strip().upper() in {"", "NONE", "NO", "N/A", "NA", "0", "NO_UNCOVERED_RESIDUE"}


def _check_equivalence(
    findings: list[dict[str, Any]],
    n0te_rows: list[dict[str, Any]],
    public_rows: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
) -> None:
    by_original = {_equivalence_original(r): r for r in receipts if _equivalence_original(r)}
    for row in [*n0te_rows, *public_rows]:
        if str(_value(row, "disposition", "current_disposition") or "").strip().upper() != "SUPERSEDED_BY_EQUIVALENT":
            continue
        req = _row_id(row)
        receipt = by_original.get(req)
        if not receipt:
            _finding(
                findings, "UNPROVEN_EQUIVALENCE", "ERROR",
                "SUPERSEDED_BY_EQUIVALENT has no equivalence receipt.", [req], "Main Steward",
            )
            continue
        missing = []
        for aliases in EQUIVALENCE_FIELDS:
            if _value(receipt, *aliases) is None or _value(receipt, *aliases) == "":
                missing.append(aliases[0])
        if missing:
            _finding(
                findings, "UNPROVEN_EQUIVALENCE", "ERROR",
                "Equivalence receipt is incomplete; semantic retirement cannot be inferred.",
                [req, _row_id(receipt)], "Main Steward", {"missing_fields": missing},
            )
            continue
        if not _residue_is_none(_value(receipt, "uncovered_residue")):
            _finding(
                findings, "UNPROVEN_EQUIVALENCE", "ERROR",
                "SUPERSEDED_BY_EQUIVALENT claims complete replacement while uncovered residue remains.",
                [req, _row_id(receipt)], "Main Steward",
                {"uncovered_residue": _value(receipt, "uncovered_residue")},
            )
        reason = str(_value(receipt, "retirement_reason") or "").strip().upper()
        if reason in {"PROVIDER_RETIRED", "AUTOMATION_RETIRED"} and not _value(
            receipt, "replacement_requirement", "replacement_requirement_id"
        ):
            _finding(
                findings, "UNPROVEN_EQUIVALENCE", "ERROR",
                "Provider/automation retirement cannot retire the underlying requirement without a retained successor.",
                [req], "Main Steward",
            )


def _acceptance_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(_value(row, "object_type") or "").strip(),
        str(_value(row, "object_id") or "").strip(),
        str(_value(row, "exact_version") or "").strip(),
    )


def _check_human_acceptance(
    findings: list[dict[str, Any]],
    state_rows: list[dict[str, Any]],
    receipt_rows: list[dict[str, Any]],
) -> None:
    accepted = {_acceptance_key(r) for r in receipt_rows if all(_acceptance_key(r))}
    for row in state_rows:
        if not _truthy(_value(row, "human_gated", "human_acceptance_required")):
            continue
        if _state(row) not in COMPLETED_STATES:
            continue
        key = (
            str(_value(row, "object_type") or "").strip(),
            str(_value(row, "object_id", "id") or "").strip(),
            str(_value(row, "exact_version", "version") or "").strip(),
        )
        if not all(key) or key not in accepted:
            _finding(
                findings, "ORPHAN_ACCEPTANCE", "ERROR",
                "Completed human-gated action lacks an exact object/version-bound human acceptance receipt.",
                [v for v in key if v], "Human Acceptance Authority",
            )


def _check_public_truth(
    findings: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    now: datetime,
) -> None:
    for row in rows:
        state = _state(row)
        if state not in PUBLIC_TRUTH_STATES:
            continue
        oid = _row_id(row) or str(_value(row, "object_id") or "<public-state>")
        evidence = _value(row, "live_evidence_ref", "provider_evidence_ref", "evidence_ref", "evidence_refs")
        if not evidence:
            _finding(
                findings, "STALE_LIVE_CLAIM", "ERROR",
                f"{state} claim has no corresponding live/provider evidence.", [oid], "Public Executor",
            )
            continue
        if _truthy(_value(row, "contradictory_evidence", "stale_contradictory_evidence")):
            _finding(
                findings, "STALE_LIVE_CLAIM", "ERROR",
                f"{state} claim has contradictory current evidence.", [oid], "Public Executor",
            )
        status = str(_value(row, "evidence_status") or "").strip().upper()
        if status == "STALE":
            _finding(
                findings, "STALE_LIVE_CLAIM", "ERROR",
                f"{state} claim is backed only by evidence explicitly classified STALE.", [oid], "Public Executor",
            )
        max_age = _value(row, "max_evidence_age_days")
        checked = _iso_datetime(_value(row, "evidence_checked_at", "evidence_last_checked", "observed_at"))
        if max_age not in (None, "") and checked is not None:
            try:
                age = (now - checked).total_seconds() / 86400
                if age > float(max_age):
                    _finding(
                        findings, "STALE_LIVE_CLAIM", "ERROR",
                        f"{state} evidence exceeds the supplied domain freshness limit.",
                        [oid], "Public Executor",
                        {"age_days": round(age, 3), "max_age_days": float(max_age)},
                    )
            except (TypeError, ValueError):
                pass


def _check_waiting(findings: list[dict[str, Any]], projections: Iterable[Any]) -> None:
    needed = {
        "blocker": ("blocker",),
        "owner": ("owner", "suggested_owner"),
        "preserved_valid_work": ("preserved_valid_work", "preserved_work"),
        "next_wake_condition": ("next_wake_condition", "wake_condition"),
        "required_proof": ("required_proof", "required_evidence"),
    }
    for projection in projections:
        for row in _rows(projection):
            if _state(row) not in WAITING_STATES:
                continue
            rid = _row_id(row) or "<waiting-item>"
            missing = [label for label, aliases in needed.items() if not _value(row, *aliases)]
            if missing:
                _finding(
                    findings, "MISSING_RESURRECTION_CONDITION", "ERROR",
                    "Waiting/debt item lacks the durable resurrection contract required to prevent silent retirement.",
                    [rid], str(_value(row, "owner") or "Main Steward"),
                    {"missing_fields": missing},
                )


def _check_orphan_acceptance_receipts(
    findings: list[dict[str, Any]],
    acceptance_rows: list[dict[str, Any]],
) -> None:
    for row in acceptance_rows:
        aid = _row_id(row) or "<acceptance>"
        if not _value(row, "trace_id") or not all(_acceptance_key(row)):
            _finding(
                findings, "ORPHAN_ACCEPTANCE", "ERROR",
                "Human acceptance receipt is missing trace identity or exact object/version binding.",
                [aid], "Human Acceptance Authority",
            )


def audit_bundle(bundle: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(bundle, Mapping):
        raise AuditInputError("audit bundle must be an object")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    findings: list[dict[str, Any]] = []

    n0te = bundle.get("n0te_requirements")
    public = bundle.get("public_scope")
    n0te_ids = _projection_ids(n0te, public=False)
    pub_ids = _projection_ids(public, public=True)

    _check_requirement_projection(findings, n0te, public=False, owner="Main Steward")
    _check_requirement_projection(findings, public, public=True, owner="Public Scope Steward")
    _check_bridges(findings, bundle, n0te_ids, pub_ids)
    integrations = _rows(bundle.get("integration_receipts"))
    _check_integrations(findings, integrations)
    _check_public_receipts(findings, _rows(bundle.get("public_receipts")))
    _check_equivalence(
        findings, _rows(n0te), _rows(public), _rows(bundle.get("equivalence_receipts"))
    )
    state_rows = _rows(bundle.get("public_current_state"))
    human_rows = _rows(bundle.get("human_acceptance_receipts"))
    _check_human_acceptance(findings, state_rows, human_rows)
    _check_orphan_acceptance_receipts(findings, human_rows)
    _check_public_truth(findings, state_rows, now=now)
    _check_waiting(findings, (n0te, public, bundle.get("public_current_state"), bundle.get("integration_receipts")))

    counts = {sev: sum(1 for f in findings if f["severity"] == sev) for sev in SEVERITIES}
    checked = [key for key in INPUT_FILES if key in bundle]
    source_versions = bundle.get("source_versions") if isinstance(bundle.get("source_versions"), dict) else {}
    return {
        "schema_version": 1,
        "report_type": "CROSS_LEDGER_INTEGRITY_REPORT",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "checked_authorities": checked,
        "source_versions": source_versions,
        "finding_counts": counts,
        "findings": findings,
        "no_automatic_mutation": True,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# CROSS LEDGER INTEGRITY REPORT",
        "",
        f"- Generated: `{report.get('generated_at', '')}`",
        f"- ERROR: **{report.get('finding_counts', {}).get('ERROR', 0)}**",
        f"- WARN: **{report.get('finding_counts', {}).get('WARN', 0)}**",
        f"- INFO: **{report.get('finding_counts', {}).get('INFO', 0)}**",
        "- Automatic mutation: **NO**",
        "",
        "## Checked authorities",
        "",
    ]
    for name in report.get("checked_authorities", []):
        version = report.get("source_versions", {}).get(name, "UNSPECIFIED")
        lines.append(f"- `{name}`: `{version}`")
    lines += ["", "## Findings", ""]
    findings = report.get("findings", [])
    if not findings:
        lines.append("No integrity findings.")
    else:
        lines += [
            "| ID | Severity | Type | Affected IDs | Suggested owner | Finding |",
            "|---|---|---|---|---|---|",
        ]
        for f in findings:
            msg = str(f.get("message", "")).replace("|", "\\|").replace("\n", " ")
            ids = ", ".join(f.get("affected_ids", [])).replace("|", "\\|")
            owner = str(f.get("suggested_owner", "")).replace("|", "\\|")
            lines.append(
                f"| {f.get('finding_id')} | {f.get('severity')} | {f.get('type')} | "
                f"{ids} | {owner} | {msg} |"
            )
    lines += [
        "",
        "## Authority boundary",
        "",
        "This auditor is read-only validation. Findings are reconciliation demands, not semantic decisions. "
        "It cannot create requirements, redefine ownership, merge code, publish, submit rights, modify providers, "
        "close incidents, or declare human acceptance.",
        "",
    ]
    return "\n".join(lines)


def load_snapshot_dir(snapshot_dir: Path) -> tuple[dict[str, Any], dict[Path, str]]:
    bundle: dict[str, Any] = {}
    hashes: dict[Path, str] = {}
    missing = []
    for key, filename in INPUT_FILES.items():
        path = snapshot_dir / filename
        if not path.is_file():
            missing.append(filename)
            continue
        hashes[path] = _sha256(path)
        try:
            bundle[key] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise AuditInputError(f"cannot read {path}: {exc}") from exc
    if missing:
        raise AuditInputError("missing required materialized projections: " + ", ".join(missing))
    manifest = snapshot_dir / "source_versions.json"
    if manifest.is_file():
        hashes[manifest] = _sha256(manifest)
        bundle["source_versions"] = json.loads(manifest.read_text(encoding="utf-8"))
    return bundle, hashes


def write_reports(
    report: Mapping[str, Any],
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / OUTPUT_JSON
    md_path = output_dir / OUTPUT_MD
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def audit_snapshot_dir(
    snapshot_dir: Path,
    output_dir: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    bundle, before = load_snapshot_dir(snapshot_dir)
    report = audit_bundle(bundle, now=now)
    write_reports(report, output_dir)
    after = {path: _sha256(path) for path in before}
    if before != after:
        raise AuditInputError("read-only violation: a semantic input projection changed during audit")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only TellMeN0TE/N0TE cross-ledger integrity auditor")
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = audit_snapshot_dir(args.snapshot_dir.resolve(), args.output_dir.resolve())
    except AuditInputError as exc:
        print(f"CROSS_LEDGER_INTEGRITY: INPUT_ERROR: {exc}")
        return 2
    print(
        "CROSS_LEDGER_INTEGRITY:"
        f" ERROR={report['finding_counts']['ERROR']}"
        f" WARN={report['finding_counts']['WARN']}"
        f" INFO={report['finding_counts']['INFO']}"
    )
    return 1 if report["finding_counts"]["ERROR"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
