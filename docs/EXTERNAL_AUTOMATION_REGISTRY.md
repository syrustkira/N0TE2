# External Automation Registry

This is the repository-side mirror of the active TellMeN0TE control-plane roles. Exact task IDs and last-run timestamps live in `TELLMEN0TE_OS`; this file preserves role semantics so repository-local reconstruction understands the external supervision graph without depending on a prior chat.

| Role | Purpose | Reads | Writes | Stop condition |
| --- | --- | --- | --- | --- |
| Portfolio Control Plane | Reconcile GitHub, Product DB, TellMeN0TE OS and worker receipts; detect stale authority, flattening, duplicate work and lifecycle defects | live N0TE2 authority, SCOPE_LEDGER, TELLMEN0TE_OS, receipts/providers | canonical volatile state and reconciliation receipts when available | retire only if superseded by a stronger canonical controller |
| N0TE Build Worker | Advance exactly one dependency-valid bounded N0TE2 increment from live handoff/controller state | handoff, current receipt, completion graph, PR/head/CI, accepted scope | governed construction branch + durable receipt; never merge/release | WAITING/BLOCKED/STABLE or no admissible construction action |
| Artist Ops Worker | Execute delegable release, public-identity, relationship and opportunity work | TELLMEN0TE_OS, Song/release objects, provider state, receipts | artist operating state and authorized provider/preparatory actions | no material artist-side action or non-delegable authority required |
| Production Knowledge Intake | Incorporate materially new production/industry knowledge without duplicating stores | web/Gmail sources, Creative & Production Playbook, N0TE knowledge surfaces | canonical playbook/knowledge destination | no material new knowledge |
| N0TE Standards Review | Detect standards/security/interoperability changes relevant to accepted scope | current standards and N0TE standards watch | canonical standards/watch findings; no automatic scope mutation | no material standards change |

## Supervision constraints

- Every worker consumes prior receipts before acting.
- An unchanged head/receipt/blocker is not a reason to repeat an action.
- Worker failure must become observable durable state rather than disappearing into chat.
- External supervision never grants merge, release, publication, purchase, destructive mutation or other consequential authority that the canonical N0TE/TellMeN0TE authority model withholds.
- Stable components leave construction mode but remain on the accepted product shelf.
- The full accepted `SCOPE_LEDGER` remains authoritative until deliberately reclassified through the canonical scope-change process.
