# N0TE Builder Exact-Head Handoff

This is the repository-side Builder handoff mechanism. It does not replace `governance/handoff.json`, `governance/current_state.json`, `governance/active_receipt.json`, the Main Steward, or the trusted Steward gate.

## Boundary

Builders implement, test and disclose. They may produce `READY_HANDOFF`, which means only that the runtime-sealed Builder manifest is complete enough for Steward qualification. It never means merged, accepted, publicly ready, rights-cleared or Steward-approved.

The exact-head design intentionally follows `governance/handoff.json`: semantic declarations may be committed, but an exact candidate SHA cannot truthfully be embedded in the commit that contains itself. `seal` therefore emits a runtime receipt bound to `git rev-parse HEAD`. Preserve that sealed JSON as CI or Steward intake evidence outside the candidate commit.

## Normal Builder flow

1. Start from a known `main` baseline and a bounded increment.
2. Create a semantic declaration:

   ```bash
   python governance/builder_handoff.py init \
     --candidate-id CANDIDATE-X \
     --builder-id BUILDER-X \
     --requirement REQ-SCOPE-NNN \
     --output /tmp/CANDIDATE-X.builder.json
   ```

3. Fill only semantic facts the Builder can truthfully assert. Do not fabricate rights, human/public acceptance, provider facts or merge authority.
4. Implement and test the candidate.
5. Seal against the exact runtime head:

   ```bash
   python governance/builder_handoff.py seal \
     --repo . \
     --declaration /tmp/CANDIDATE-X.builder.json \
     --baseline-sha "$BASELINE_SHA" \
     --current-main-sha "$CURRENT_MAIN_SHA" \
     --expected-head "$(git rev-parse HEAD)" \
     --pr "$PR_NUMBER" \
     --output "$RUNNER_TEMP/CANDIDATE-X.H0.handoff.json"
   ```

6. If the result is `HANDOFF_BLOCKED`, repair the exact listed omissions. If the candidate head changes, the old receipt remains historical and a fresh seal is mandatory.
7. Hand the sealed receipt to the Main Steward and continue dependency-safe work elsewhere. Do not poll the PR waiting for merge.

`seal` refuses to run from branch `main` and performs no Git mutation. It only reads repository state and writes the requested output receipt.

## Risk-aware validation

`TIER_0` permits a structural/docs handoff without fake provider or unrelated test fields. `TIER_1` requires exact-head test receipts. `TIER_2` additionally requires regression and consumer-smoke proof. `TIER_3` additionally requires full regression and recovery proof, with migration, rights, public, security/privacy and cross-platform proof required when those domains are actually affected.

A detected schema/migration path requires migration and recovery evidence. A changed image/audio/font asset requires rights/provenance records. `PUBLIC_IMPACT_PRESENT` requires explicit public domains/assets/providers, rights, privacy/security, accessibility, deployment, state/migration, rollback and human-acceptance declarations. These are declarations of consequence, not public PASS.

## FIX, REBUILD and SPLIT orders

A Steward order creates a new handoff version rather than overwriting the old receipt:

```bash
python governance/builder_handoff.py successor \
  --previous /evidence/CANDIDATE-X.H0.handoff.json \
  --order-type FIX_ORDER \
  --order-id FIX-123 \
  --output /tmp/CANDIDATE-X.H1.builder.json
```

The successor preserves requirement identity, prior handoff version, prior exact head and order reference, clears stale test receipts, and requires fresh exact-head proof. `REBUILD_ORDER` uses the same lineage mechanism against a refreshed baseline. `SPLIT_ORDER` preserves the original candidate lineage while extracted portions receive their own exact heads and requirement mappings.

## Steward intake

The Steward can consume any set of sealed handoff files without scraping PR prose:

```bash
python governance/builder_handoff.py intake /evidence/handoffs --output /tmp/steward-intake.json
```

The intake view exposes candidate, latest handoff version, exact head, requirements, risk, dependencies, collision surfaces, tests, review status, public impact, limitations, Builder and handoff state.

Only the Steward or trusted authority layer may add integration disposition. A Builder's `READY_HANDOFF` never changes `authority_verification.steward` from `NOT_EVALUATED`.

## Cross-Ledger Auditor interface

```bash
python governance/builder_handoff.py audit /evidence/handoffs \
  --dispositions /evidence/steward-dispositions.json \
  --output /tmp/builder-audit.json
```

The trace rows expose candidate, requirement IDs, baseline, head, handoff state, Steward disposition, successor residue, public impact and evidence refs. The interface flags blocked/stale candidates without disposition, deferred residue without a successor, invalid handoffs, and public-impact work that still needs a separate public disposition.

This is intentionally an interface rather than a second requirement database. Canonical requirement meaning remains in existing N0TE authority.

## Existing CI

The current `N0TE2 Governance` workflow already executes the full `tests` tree on Linux, Windows and macOS, so `tests/governance/test_builder_handoff.py` becomes exact-head cross-platform regression automatically without modifying privileged workflow files.

Wiring a live candidate declaration into the trusted/base-owned Steward status is a separate Main Steward integration step because `.github/workflows/*` and the trusted Steward checker are protected governance surfaces. During an active Steward incident repair, a Builder must not bypass that boundary by editing the privileged workflow merely to make this feature appear integrated.

## Legacy/open PR migration

Use `migrate` with evidence facts only. The classifier returns one of `COMPLETE_HANDOFF`, `PARTIAL_HANDOFF`, `RECONSTRUCTABLE`, `STEWARD_REVIEW_REQUIRED`, `STALE`, or `SUPERSEDED`. Missing historical facts stay missing. Do not backfill invented tests, rights, acceptance or exact-head evidence.
