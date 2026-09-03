# Startup Reconciliation

This is the minimum reconstruction protocol for any external ChatGPT/controller session operating N0TE2 with TellMeN0TE.

The governing boot doctrine is `docs/MASTER_CONTINUITY_CONTROLLER.md`. Read it before substantial continuation work. This file defines the minimum live-state reconstruction sequence beneath that doctrine.

## Read order

0. `docs/MASTER_CONTINUITY_CONTROLLER.md` for control-plane behavior, memory separation, anti-loop, anti-flattening, whole-history audit triggers, semantic deduplication, execution bias and local/cloud boundaries.
1. Repository durable handoff and the exact authority references it names, beginning with `governance/handoff.json`, `governance/current_state.json`, `governance/active_receipt.json`, `governance/completion_graph.json`, `governance/invariants.json` and `governance/requirements.json`.
2. Bind that reconstructed durable state to the live N0TE2 open construction PR/head and exact-head CI/status evidence. GitHub delivery evidence verifies or contradicts reconstructed state; it does not frame product truth before reconstruction.
3. `N0TE_PRODUCT_DB` canonical `SCOPE_LEDGER` and current governance controller row.
4. `TELLMEN0TE_OS`, including `CONTROL_PLANE` and active artist/release state.
5. Relevant worker receipts and connected provider state.
6. Historical conversation/files only when required to resolve missing provenance or contradiction, or when the Master Continuity Controller triggers a whole-history audit.

## Output contract

A continuation should normally produce only:

- CURRENT STATE
- DELTA
- OPEN LOOPS
- ACTIONS TAKEN when work was actually performed
- NEXT MOVE

Do not restart from a broad history recap when the durable sources above are reachable.

## Staleness handling

If reconstructed repository authority, live GitHub delivery evidence and Drive volatile controller state disagree, preserve the disagreement as a control-plane defect. The durable handoff establishes the intended lifecycle/selection claim, live GitHub binds whether that claim is actually present and verified at the observed head, and the volatile Drive controller row is then reconciled. Never overwrite accepted product scope with PR/CI facts.

## Scope handling

The complete accepted product remains defined by canonical scope. A selected vertical increment only determines execution order. It never authorizes omission of other accepted capabilities.
