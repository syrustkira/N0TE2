# CORE-02A implementation evidence

This bounded increment implements Song Session intent, scratch exploration, explicit promotion, debrief, next action and restart continuity on top of the closed CORE-01 canonical memory substrate.

Evidence boundaries:

- Scratch is durable Session chronology, not EvidenceMemory.
- Only explicit promotion creates a canonical EvidenceClaim.
- Promotion request and claim/link semantics are designed so an interrupted claim write can leave a retryable request but not an unlinked durable doctrine claim.
- Session identity/objective are immutable; close is one-way; Session rows/scratch/promotion requests/promotion links cannot be deleted or rewritten.
- Activity remains the chronology owner; Session mutations journal into Activity rather than creating a parallel event store.
- One open Session per Song is allowed; another Song can have its own open Session.
- Cross-Song version or promotion scope is rejected.
- CORE-03 remains dependency-ready and is not semantically subordinated to CORE-02.

Automated source tests are in `tests/core/test_session.py`; stage-level normal-path verification is in `governance/smoke/consumer_smoke.py`. Hosted exact-head evidence is not assumed green when connector status is absent.
