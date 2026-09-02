# N0TE External Control-Plane Bridge

This file defines the boundary between repository-local N0TE2 construction authority and the broader TellMeN0TE/ChatGPT operating system.

## Authority order

1. Live repository handoff/controller, open PR/head, exact-head CI and completion graph determine N0TE2 construction truth.
2. `N0TE_PRODUCT_DB` / `SCOPE_LEDGER` preserves the complete accepted product shelf and anti-flattening semantics.
3. `TELLMEN0TE_OS` carries portfolio-level Artist + N0TE operational state and current cross-system actions.
4. Background workers consume those authorities and emit durable receipts. Conversation history is fallback evidence only.

## Anti-flattening contract

Vertical integration is a sequencing and proof strategy, not a product-scope reduction strategy. A bounded active increment never removes, demotes, or implicitly defers accepted SCOPE_LEDGER capabilities. "No new feature category without justification" prevents uncontrolled scope expansion; it does not excuse omission of already-accepted requirements.

## External workers

The broader operating system may run bounded workers for:

- portfolio/control-plane reconciliation;
- N0TE construction execution;
- artist operations and provider follow-through;
- production/industry knowledge intake;
- standards/compatibility watch.

Every worker must have a purpose, source authority, bounded write authority, wake condition, stop/retire condition and durable receipt path. Workers must consume prior receipts before acting and must not repeat work against an unchanged head/blocker without new evidence.

## Reconciliation rule

A GitHub -> Drive disagreement is a control-plane defect. The next capable controller must refresh live GitHub truth first and reconcile the volatile Drive controller state. Durable scope remains in SCOPE_LEDGER and must not be overwritten by volatile PR/CI facts.

## Startup rule

A fresh ChatGPT/N0TE control cycle should reconstruct in this order:

1. repository handoff/controller + open PR/head + exact CI;
2. Product DB accepted scope/governance;
3. TellMeN0TE OS portfolio state;
4. worker/automation receipts and provider state relevant to the active lane;
5. historical conversation/archive only when authority is missing or contradictory.

The intended outcome is continuous construction and artist execution without repeated chat archaeology, while preserving the complete designed product.
