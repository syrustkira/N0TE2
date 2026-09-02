# External Automation Registry

This is the repository-side mirror of the TellMeN0TE external control-plane topology. Exact automation IDs and latest operational state live in `TELLMEN0TE_OS`; `governance/automation_registry.json` is the machine-readable supervision authority. This file preserves human-readable role semantics without depending on prior chat.

## Current topology

| Role | State | Purpose | Reads | Writes / authority | Stop or wake condition |
| --- | --- | --- | --- | --- | --- |
| TellMeN0TE + N0TE Master Controller | ACTIVE | Single cloud-side coordinator for reconstruction, semantic deduplication, cross-surface reconciliation, safe connected execution, artist/release/profile/opportunity follow-through, due knowledge/standards/security/trajectory checks, unknown-unknown scanning and local/cloud handoff | live N0TE2 authority, N0TE_PRODUCT_DB/SCOPE_LEDGER, TELLMEN0TE_OS, prior receipts, relevant Drive/connected providers/current web | safe authorized connected work plus canonical operational/reconciliation receipts; never unauthorized merge/release/publish/spend/irreversible action | hourly/material-change wake; retire only when a stronger canonical local+cloud controller replaces it |
| N0TE Build Executor | DORMANT / ON DEMAND | Execute or delegate one exact dependency-valid bounded N0TE2 construction increment after explicit current-head selection by canonical governance/master controller | master controller, handoff, active receipt, completion graph, exact PR/head/CI, accepted scope | receipt-bounded repository/delegation work; never independently select adjacent work or merge/release | explicit current-evidence delegation; return dormant/block/wait after one bounded attempt |
| GitHub Governance Verifier | ACTIVE / EVENT DRIVEN | Independently verify exact-head governance, regression and staged consumer evidence | repository exact head | verification artifacts/status only | each relevant repository event |
| Construction Controller | ACTIVE while construction lifecycle is ACTIVE | Select and bound justified N0TE construction from canonical acceptance/scope state | completion graph, current state, receipt, evidence | governed selection/receipt authority | becomes dormant at STABLE/WAITING/BLOCKED as defined by governance |

The former standalone Artist Ops Worker, Production Knowledge Intake, N0TE Standards Review, profile watcher and opportunity watcher are not separate active cloud lanes. Their useful jobs are invoked as bounded subroutines of the master controller when materially relevant or due. Do not recreate equivalent watchers under new names without measured evidence that dedicated isolation reduces total supervision burden.

## Supervision constraints

- `docs/MASTER_CONTINUITY_CONTROLLER.md` governs cloud continuation behavior.
- Every actor consumes prior receipts before acting.
- Different wording that seeks the same outcome maps to the same canonical work object.
- An unchanged head/receipt/blocker is not a reason to repeat an action.
- Repeated discussion or repeated agent failure triggers diagnosis of the missing execution/state/harness/tool/authority layer rather than harder reprompting.
- Whole-history archaeology is bounded fallback/assimilation work, not normal startup.
- Worker failure becomes observable durable state rather than disappearing into chat.
- External supervision never grants merge, release, publication, purchase, destructive mutation or other consequential authority that the canonical N0TE/TellMeN0TE authority model withholds.
- Stable components leave construction mode but remain on the accepted product shelf.
- The full accepted `SCOPE_LEDGER` remains authoritative until deliberately reclassified through the canonical scope-change process.
