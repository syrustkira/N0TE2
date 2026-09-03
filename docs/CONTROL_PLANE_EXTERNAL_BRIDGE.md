# N0TE External Control-Plane Bridge

This file defines the boundary between repository-local N0TE2 construction authority and the broader TellMeN0TE/ChatGPT operating system. `docs/MASTER_CONTINUITY_CONTROLLER.md` is the canonical external boot/continuity doctrine.

This bridge is an implementation/coordination contract beneath accepted N0TE scope. It does not create a second product scope database.

## Authority order

Authority is domain-specific rather than one flat list.

- The TellMeN0TE Master Context is the HUMAN CONSTITUTION for durable person/life/career/portfolio mission, hierarchy, boundaries and reasons. Lower layers may not silently redefine higher-layer purpose.
- Live repository handoff/controller, open PR/head, exact-head CI and completion graph determine N0TE2 construction truth.
- `N0TE_PRODUCT_DB` / `SCOPE_LEDGER` preserves the complete accepted N0TE product shelf and anti-flattening semantics.
- `TELLMEN0TE_OS` carries artist/business operational state and derived portfolio actions.
- The single active cloud master controller reconciles those authorities and emits durable receipts; bounded specialists/executors are invoked only when justified.
- Conversation history is fallback/provenance evidence, not normal live authority.

The permanent upward lineage is:

`PERSON / LIFE → CAREER / PORTFOLIO → DOMAIN → PROJECT / PRODUCT → CUSTOMER / ARTIST OUTCOME → JOURNEY → CAPABILITY → IMPLEMENTATION → TASK`

No lower layer may redefine a higher layer merely because it is easier to measure or currently active.

## Anti-flattening contract

Vertical integration is a sequencing and proof strategy, not a product-scope reduction strategy. A bounded active increment never removes, demotes, or implicitly defers accepted `SCOPE_LEDGER` capabilities. "No new feature category without justification" prevents uncontrolled scope expansion; it does not excuse omission of already-accepted requirements.

Retained/alive, background-active and foreground execution are distinct states. A lane can remain alive or background-maintained without becoming a current human burden. Narrow foreground focus must not be mistaken for deactivation of the rest of the portfolio.

## Startup entrypoint matrix

Every actor has one deterministic entrypoint. No actor should invent a competing catch-up routine.

| Actor | First entrypoint | Then reads | May select work? |
| --- | --- | --- | --- |
| Human / fresh ChatGPT session | Drive `START HERE - TELLMEN0TE + N0TE` or the user simply says `CONTINUE` | `docs/MASTER_CONTINUITY_CONTROLLER.md`; for broad portfolio decisions also the current Human Constitution/Master Context; then only live/canonical sources relevant to the request | Master controller may select non-RED safe work under its doctrine |
| Scheduled cloud controller | automation instruction referencing `docs/MASTER_CONTINUITY_CONTROLLER.md` | prior master receipt; Human Constitution when portfolio priority/meaning is implicated; relevant live GitHub/Product DB/TELLMEN0TE_OS/provider evidence | yes, within bounded cloud authority; no unauthorized publish/merge/release/spend/destructive action |
| Fresh coding agent | repository `AGENTS.md` | Master Continuity Controller once, then `governance/handoff.json`, generated reconstruction refs, current state, active receipt, exact head/CI | only the repository construction controller/active receipt may authorize implementation |
| Local N0TE runtime | local bootstrap/current Artist/Song/session state | local durable memory/checkpoint + current provider/DAW evidence; shared constitutional/semantic references and cloud state only through the bridge when needed | yes within local N0TE authority/action plane; external consequences remain approval/authority gated |
| Bounded coding executor | explicit delegation/control envelope | exact receipt, baseline/head, affected implementation/tests | no global reselection; execute one bounded task and return receipt |
| External provider/tool adapter | bounded tool invocation | exact target/current provider state and authority | no product/portfolio selection; perform only the requested authorized operation |

Startup documents are routing maps, not volatile truth. They must point to current authority instead of duplicating it.

## External controller topology

The default external topology is deliberately small:

- one active TellMeN0TE + N0TE Master Controller for reconstruction, semantic deduplication, portfolio reconciliation, due sensing/maintenance, safe connected execution and local/cloud routing;
- one dormant/on-demand N0TE Build Executor that may be explicitly reactivated only for a current receipt-bounded coding task;
- repository-native GitHub governance verification and construction selection remain separate because they are authoritative internal governance roles, not competing cloud assistants;
- provider-native automation, local N0TE jobs, connected apps, Work/Cloud Browser, CI and human specialists remain legitimate execution mechanisms beneath the shared control plane when they are the best executor.

**One control plane does not mean one universal super-agent or one universal executor.** The control plane preserves shared meaning, ownership, routing, reconciliation and receipts. Each background obligation should run on the narrowest reliable legitimate executor with the least authority necessary. Do not rebuild a provider's durable infrastructure merely so the controller can claim to own the execution mechanism.

Do not recreate standalone overlapping cloud watchers merely because their labels differ. A dedicated specialist is justified only when measured workload, isolation, authority, latency or reliability needs outweigh the supervision cost of another actor.

Every actor must have a purpose, source authority, bounded write authority, wake condition, stop/retire condition and durable receipt path. Actors consume prior receipts before acting and must not repeat work against unchanged evidence.

## Layer communication model

Layers do not share one giant mutable prompt or database. They cooperate through stable semantic identity, referenced canonical state, bounded execution claims and durable messages/receipts.

The minimum logical actors are:

- `HUMAN`
- `CLOUD_CONTROLLER`
- `LOCAL_N0TE`
- `CONSTRUCTION_CONTROLLER`
- `CODING_EXECUTOR`
- `PROVIDER_ADAPTER`

A layer should know enough about another layer to route work and verify results, but should not silently inherit the other layer's authority, private state or implementation details.

### One-active-owner / execution-claim law

For consequential, externally mutating, long-running or collision-prone work, exactly one executor owns the active attempt at a time.

A durable claim/lease identifies:
- the semantic object/job;
- the executor;
- the bounded scope;
- the state/version observed when ownership began;
- acquisition and expiry/release conditions;
- idempotency key;
- expected completion/failure/handoff receipt.

Other layers may inspect, advise, prepare or monitor the same job, but they must not independently perform the same consequential side effect until the claim is reconciled, released or safely expired. An executor disappearing must not strand the job forever; ownership is recoverable according to the job's failure policy.

### Control Plane Envelope

Cross-layer work that must survive process/session boundaries should use a machine-readable envelope with equivalent semantics to:

```json
{
  "protocol_version": "1",
  "message_id": "stable-unique-id",
  "correlation_id": "one-user-outcome-or-task-chain",
  "trace_id": "cross-layer-observability-id",
  "source_actor": "CLOUD_CONTROLLER",
  "target_actor": "LOCAL_N0TE",
  "semantic_scope": {
    "artist_id": null,
    "song_id": null,
    "journey_id": null,
    "capability_id": null,
    "task_id": null,
    "receipt_id": null
  },
  "intent": "plain-language desired outcome",
  "mode": "READ|ADVISE|PROPOSE|EXECUTE|VERIFY",
  "state_basis": {
    "authority_refs": [],
    "versions_or_digests": [],
    "observed_at": null
  },
  "authority": {
    "classification": "GREEN|AMBER|RED",
    "approval_required": false,
    "approval_ref": null
  },
  "execution_claim": {
    "required": false,
    "claim_id": null,
    "owner": null,
    "acquired_at": null,
    "expires_at": null,
    "expected_state_revision": null
  },
  "idempotency_key": "stable-key-for-the-intended-side-effect",
  "payload_refs": [],
  "evidence_refs": [],
  "status": "READY|RUNNING|WAITING|BLOCKED|DONE|UNKNOWN",
  "wake_condition": null,
  "result_receipt_ref": null
}
```

The exact transport/serialization may evolve. The semantic contract must survive transport changes.

### Envelope rules

1. Consequential or long-running cross-layer work is durably represented before side effects begin.
2. Collision-prone work acquires one active execution claim/lease before the consequential attempt.
3. Every side effect uses an idempotency key or equivalent recurrence guard so retries are safe.
4. The receiver refreshes volatile target state immediately before mutation rather than trusting a stale sender snapshot.
5. Authority cannot be transferred merely by writing it into a message. The receiver enforces its own canonical authority rules.
6. `UNKNOWN` is a valid result and must never be promoted to success without evidence.
7. The result of meaningful work is a receipt/artifact, not only conversational prose.
8. Stable semantic IDs link the same Artist/Song/journey/capability/task across local and cloud layers.
9. Large files/audio/artifacts are referenced by durable handles rather than copied into every message.
10. Trace/correlation IDs make one user outcome reconstructable across cloud, local, coding and provider hops.
11. Messages are communication. Canonical domain state remains in its owning store.
12. Receivers must tolerate duplicate delivery and process an unchanged envelope at most once unless new evidence changes the action.
13. Protocol version/capability negotiation must fail visibly rather than silently dropping semantics.
14. Shared-brain writes should use current revision/version/conflict checks where supported. A stale writer re-reads and reconciles rather than silently overwriting a newer branch result.
15. Before asking the artist for a fact, preference, decision or approval, retrieve any existing durable answer and re-ask only when the previous answer is missing, stale, differently scoped, invalidated or consequential approval genuinely must be renewed.

## Tool/agent protocol guidance

Use protocols by job rather than forcing one protocol to own the entire system.

- MCP-style resources/tools are a strong fit for exposing bounded local or remote capabilities and context to an AI host while retaining host-side permission/security boundaries.
- Peer or long-running agent delegation needs task/message/artifact semantics, correlation, durable status and reconnectable receipts. A2A-compatible concepts may be reused where mature and useful rather than creating bespoke semantics unnecessarily.
- Durable workflow/checkpoint infrastructure may own retries, resume and long-running execution, but it must not become the N0TE product semantic authority.

Provider/protocol choice remains an implementation decision beneath the stable Control Plane Envelope unless deliberately promoted through product governance.

## Local/cloud state exchange

The cloud controller should normally send local N0TE only:
- desired outcome;
- stable semantic scope;
- relevant authority/canonical references;
- exact requested mode;
- required evidence/completion condition;
- current approval state;
- active execution claim when consequential ownership is required.

Local N0TE should return:
- refreshed local/DAW truth used;
- action/proposal actually performed;
- verification evidence;
- changed canonical/local state refs;
- claim release/handoff state;
- unresolved blocker/wake condition;
- durable receipt.

Raw local private state is not uploaded merely because it exists. Shared brain does not mean universal access. Retrieval remains partitioned by Artist, Song, client/project, private material, credentials, sensitive domains and external-provider consent, and follows the minimum-needed principle.

## Reconciliation rule

A GitHub -> Drive disagreement is a control-plane defect. The next capable controller refreshes live GitHub truth first and reconciles volatile Drive controller state. Durable scope remains in `SCOPE_LEDGER` and must not be overwritten by volatile PR/CI facts.

Different wording that seeks the same outcome is also a reconciliation problem, not a reason to create a second task. The master controller normalizes the intended result before creating work.

Branch disagreement is not resolved by whichever actor spoke last. Verified facts remain source-backed, hypotheses remain labeled, conflicting recommendations expose the material tradeoff, and the broad controller synthesizes cross-domain conflict before escalating only the irreducibly human decision.

## Whole-history rule

Historical conversations, old branches/PRs, archived Drive material and prior prompts may be inspected when provenance is missing, sources contradict, the user explicitly asks to review everything, or a supposedly solved outcome keeps returning. The purpose is assimilation into canonical state, not a repeated broad recap. After assimilation, normal startup returns to durable/live authority.

## Stop-polishing / controller freeze gate

The control-plane doctrine is considered sufficiently specified for implementation when all of the following are true:

- human startup has one durable entrypoint that resolves to the canonical master controller;
- coding-agent startup resolves through `AGENTS.md` to the same master doctrine plus live repository handoff/current state/receipt;
- one cloud master controller is active and registry/runtime state agree;
- authority hierarchy and anti-flattening rules are explicit;
- whole-history assimilation has bounded triggers rather than being normal startup;
- semantic deduplication/anti-loop behavior is explicit;
- local/cloud ownership and cross-layer message/receipt semantics are explicit;
- one-active-owner/claim, safe retry/idempotency and conflict-safe write behavior are explicit;
- accepted product scope remains canonical in `SCOPE_LEDGER` rather than duplicated into the controller;
- meaningful side effects have authority/idempotency/evidence rules;
- no known unowned control-plane contradiction remains;
- exact-head governance for these changes is green, or the remaining failure is a truthfully isolated implementation blocker with an owner/wake condition.

Once those conditions are met, **stop changing prompts/controller doctrine because another phrasing seems nicer.** Reopen controller/meta-governance only when there is observed evidence of:

- continuation/context reconstruction failure;
- semantic flattening or duplicate-work recurrence;
- contradictory authority/state;
- security/privacy/rights defect;
- changed external protocol/provider constraint that invalidates the contract;
- explicit user/product-intent change;
- missing observability/harness capability preventing truthful operation.

Otherwise, new ideas go to implementation, accepted product scope or evidence-driven backlog, not another master prompt revision.

## Startup rule

A fresh ChatGPT/N0TE control cycle reconstructs in this order appropriate to the decision:

0. `docs/MASTER_CONTINUITY_CONTROLLER.md`;
1. Human Constitution/Master Context when broad portfolio purpose, tradeoffs or cross-domain selection are implicated;
2. repository handoff/controller + open PR/head + exact CI when N0TE construction is relevant;
3. Product DB accepted scope/governance;
4. TellMeN0TE OS artist/business operational state;
5. relevant master receipt/provider/local state;
6. historical conversation/archive only when the controller's whole-history trigger is satisfied.

The intended outcome is continuous construction and artist execution without repeated chat archaeology, while preserving the complete designed product and returning human attention.
