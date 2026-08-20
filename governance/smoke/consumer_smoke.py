#!/usr/bin/env python3
"""Pre-product smoke harness.

During BOOT-02 and LEGACY-01 there must be no N0TE2 product implementation.
This makes that absence executable truth while governance and migration evidence
are constructed.
"""
from pathlib import Path
import sys

repo = Path(__file__).resolve().parents[2]
for forbidden in ("app", "src", "n0te2", "legacy"):
    path = repo / forbidden
    if path.exists() and any(path.rglob("*")):
        print(f"PRE-PRODUCT SMOKE: RED: product/direct-legacy implementation appeared early: {forbidden}/", file=sys.stderr)
        raise SystemExit(1)
print("PRE-PRODUCT SMOKE: GREEN: governance/migration-evidence-only repository surface")
