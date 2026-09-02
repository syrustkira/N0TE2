# N0TE2 Autonomous Construction Contract

## Canonical startup entrypoints

A fresh coding agent must not invent its own catch-up path.

1. Read `docs/MASTER_CONTINUITY_CONTROLLER.md` once at session/continuation startup for the global continuity, anti-loop, anti-flattening, memory, local/cloud and stop-polishing doctrine.
2. Read `governance/handoff.json` for the current repository construction handoff.
3. Run `python governance/build_handoff.py --repo . --check` to bind the handoff to the exact current Git head.
4. Read every `reconstruction.required_refs` entry named by the handoff.
5. Verify `governance/current_state.json`, the completion graph and `governance/active_receipt.json` agree with the handoff lifecycle.
6. Read `docs/STARTUP_RECONCILIATION.md` whenever the work involves external ChatGPT/cloud state, cross-system continuation, stale Drive/GitHub state, whole-history assimilation or local/cloud handoff.
7. Only then inspect relevant implementation and tests.

`docs/MASTER_CONTINUITY_CONTROLLER.md` governs how work is reconstructed and continued. `governance/handoff.json`, current state, active receipt, exact head and exact-head evidence govern what construction is actually authorized now. The master controller never overrides live repository construction truth or accepted `SCOPE_LEDGER` semantics.

Do not make the artist rewrite the product specification, restate prior decisions or reconstruct prior chat merely to start a coding session.

## Product hierarchy

`ARTIST -> SONG / PROJECT -> N0TE HEADQUARTERS -> INTENT / JOB -> CAPABILITY -> DAW / TOOL / PROVIDER -> VERIFIED RESULT -> MEMORY -> NEXT ACTION`

N0TE is Headquarters. A DAW is a creative workspace and execution provider beneath the Song.

## Reconstruct before editing

Normal startup is deterministic. Do not begin by searching old chats, PRs or adjacent commits.

Historical repositories, PR descriptions, old closure tables, old prompts, implementation order, last commit and nearest failing test are evidence only. Invoke historical reconstruction only when durable authority is missing or contradictory, the Master Continuity Controller explicitly triggers a whole-history audit, or provenance is required to resolve a repeated loop. Historical evidence never silently selects the next N0TE2 job.

If the same goal, bug or design question repeatedly returns, treat the repetition as a controller/harness/state defect to diagnose. Do not simply regenerate the prior plan or reprompt harder.

## Supervision graph

The artist is root authority. N0TE supervises delegated automation. Workers and controllers never expand their own authority or redefine their parent purpose.

Every autonomous actor must be registered in `governance/automation_registry.json` with a stable ID, parent, purpose, wake condition, observability contract and retirement condition. A dormant actor waking is an observable event, never silent resumption. Current observations bind the exact repository head.

External/cloud actor topology is mirrored in `docs/EXTERNAL_AUTOMATION_REGISTRY.md`; communication boundaries are defined in `docs/CONTROL_PLANE_EXTERNAL_BRIDGE.md`. Do not create a new worker or agent lane merely because a task is phrased differently.

## Lifecycle

Construction is temporary. `ACTIVE`, `STABLE`, `WAITING` and `BLOCKED` are legal controller lifecycle states.

- `ACTIVE` requires exactly one graph node selected through current state and a bounded active receipt.
- `STABLE`, `WAITING` and `BLOCKED` require zero ACTIVE graph nodes and no construction authority.
- `WAITING` and `BLOCKED` require an explicit wake condition.
- `STABLE` requires a terminal reason: no currently justified construction work exists.
- Reactivation requires a declared trigger, an observable state transition and fresh global selection.

The destination of justified construction is stable operation, not another construction task.

Controller/prompt polishing is also construction. Once the Master Continuity Controller freeze conditions are satisfied, do not modify controller doctrine because a nicer phrasing is available. Reopen meta-governance only for observed continuation/context failure, contradictory authority, a material security/privacy/rights defect, a changed external constraint/capability, explicit product-intent change, or evidence that the current controller cannot complete its job.

## Scope versus selection

Known unfinished scope is not selected work. Requirements may remain `KNOWN` and still block candidate completeness without becoming ACTIVE. Work becomes active only after dependency-ready global selection plus a bounded receipt.

After closure or blockage, reconcile evidence and reselect globally from the whole graph. Do not continue by adjacency, subsystem momentum or implementation convenience.

Do not copy the entire `SCOPE_LEDGER` into prompts or agent contracts. Retrieve the relevant accepted capabilities from the canonical scope store. The controller preserves scope; it does not become a second scope database.

## Durable decisions and evidence

Constitutional rules live in `governance/invariants.json`. Consequential choices, incidents, controller changes, definitions, trajectory audits and intent provenance live in the append-oriented durable ledgers referenced by `governance/handoff.json`.

Derived facts may be reconciled from newer evidence, but supersession history must remain visible. Constitutional definitions such as authority, completion, mutation rights, lifecycle semantics and approval meaning cannot silently drift.

## Autonomy

- `GREEN`: decide, implement, test, continue within delegated scope.
- `AMBER`: decide professionally, record rationale, keep reversible.
- `RED`: escalate for product identity/scope changes, destructive or irreversible behavior, privacy/rights, money/spend, surprise publication/external action, major security boundaries, or enduring subjective brand/artist decisions.

Do not ask the artist merely because information is incomplete. Ask only when every legitimate autonomous path is blocked or RED authority is required.

## Anti-flattening invariants

- Implementation maturity must never mutate product scope.
- Historical implementation order has zero semantic authority.
- All six named DAWs are peer deep targets.
- `GENERIC_OTHER` is not manual-only N0TE.
- macOS, Windows and Linux are peer consumer platforms.
- Apple Silicon/Intel, Windows x64/ARM64, Linux x86_64/ARM64 are real core architecture obligations.
- OS version names alone never raise minimum versions.
- VST3 implementation maturity must not flatten plug-in scope.
- Held future capabilities cannot become active because they are convenient to implement.

## Systemic repair

A fix is not closed until it covers: instance -> root cause -> sibling scan -> consequence scan -> recurrence scan -> regression guard -> consumer outcome.

A green previous head does not prove the current head. Exact-head cross-platform evidence must be refreshed whenever the repository head changes.
