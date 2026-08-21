# N0TE2

N0TE2 is the clean-room construction repository for N0TE, a Song-centered persistent Artist Headquarters.

This repository is intentionally in **BOOT-02**: executable construction governance only. No legacy or product-feature implementation is admitted until the governance harness is exact-head green and the active receipt advances to `LEGACY-01`.

## Current construction law

- Product semantics come from the canonical N0TE Blueprint and Product DB, mirrored here only as machine-enforceable contracts.
- Implementation maturity never changes semantic scope.
- All six core DAWs are peer `DEEP` targets: Ableton Live, FL Studio, Logic Pro, Pro Tools, Studio One, REAPER.
- Other DAWs retain a substantial truthful N0TE baseline (`DAW-07`).
- macOS, Windows, and Linux are peer consumer platforms; all three block customer-mode handoff.
- Core architecture acceptance includes Apple Silicon + Intel Mac, Windows x64 + ARM64, Linux x86_64 + ARM64.
- Plug-in capability is universal N0TE capability, not one DAW or one format. Standard scan paths plus user-added custom paths are required.
- Held future services cannot self-activate.
- Missing future licenses/devices/accounts/certificates can block only their exact evidence claim, never unrelated construction.

Run:

```bash
python governance/check_governance.py --repo .
python -m unittest discover -s tests -p 'test_*.py'
```
