# Cross-Ledger Integrity Auditor

## Authority boundary

The Cross-Ledger Integrity Auditor is an integrity observer and defect router. It is not a Main Steward, Builder, semantic authority, public executor, rights authority, or source of product/public truth.

It normalizes source-owned records into an audit graph, evaluates invariants, persists attributable findings and run receipts in its output state, localizes any blocking cone, and routes remediation back to the authority that owns the broken edge. It never silently repairs canonical truth.

## Runtime inputs

The local N0TE2 adapter reads the existing governance authorities rather than creating a second product database:

- `governance/requirements.json`
- `governance/completion_graph.json`
- `governance/current_state.json`
- `governance/active_receipt.json`
- `governance/authority.json`
- `governance/invariants.json`
- `governance/automation_registry.json`
- optional incident/provenance/decision/definition history

Public runtime, provider, rights/provenance, and human-acceptance truth arrive as normalized external snapshots. `governance/integrity_external_snapshot.schema.json` defines the interchange surface. Missing required external evidence produces `INCOMPLETE_AUDIT`, never PASS.

## Normalized graph

The audit graph preserves stable source identities and supports requirement, public requirement, candidate, commit, main state, implementation/merge/completion/public/equivalence receipts, public handoff/deployment/failure, asset/version, rights evidence, provider action, observation, human acceptance, supersession, tombstone, incident, actor, and trace objects.

Source ledgers do not have to adopt this schema. Normalization belongs only to the audit layer.

## Event and full reconciliation

The CLI supports:

```sh
python governance/integrity_auditor.py --repo . --output-dir integrity-runtime --print-summary
python governance/integrity_auditor.py --repo . --event-path governance/requirements.json --output-dir integrity-runtime
python governance/integrity_auditor.py --repo . --external-snapshot /path/public-runtime.json --output-dir integrity-runtime
python governance/integrity_auditor.py --repo . --baseline --output-dir integrity-runtime
```

Known governance-path events map to affected graph cones. Unknown event paths deliberately fall back to a full audit rather than risk an incorrectly narrow cone. Daily full reconciliation remains the backstop for missed events, stale indexes, manual edits and external drift.

## Durable outputs

Each run emits machine-readable state:

- `integrity_run_receipt.json`
- `integrity_run_index.jsonl`
- `integrity_findings.jsonl`
- `integrity_graph.json`
- `integrity_summary.json`
- `remediation_queue.json`

Finding IDs are deterministic across runs. Prior state is folded forward. Open findings never disappear merely because a later run cannot inspect the required source. A finding resolves only after a sufficiently complete revalidation does not reproduce it. Terminal false-positive/supersession history remains visible.

The GitHub Actions runner carries the complete prior artifact forward into the next run, so the current artifact contains historical finding lifecycle rather than only this run's deltas.

## Blocking law

Only cataloged blocking/critical invariant failures generate blocking cones. The cone is graph-local. Unrelated work remains outside the block. `INCOMPLETE_AUDIT` does not masquerade as PASS and does not globally freeze unrelated work by itself.

## Tests

`tests/governance/test_integrity_auditor.py` covers the required orphan, stale-branch, receipt, public handoff, rights, supersession, contradiction, acceptance, staleness, authority-collision, durable-history and localized-blocking cases, plus malformed records and historical range migration.
