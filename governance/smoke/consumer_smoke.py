#!/usr/bin/env python3
"""BOOT-02 smoke harness.

At BOOT-02 there must be no product implementation to 'smoke test'. This script
exists to make that absence executable truth rather than an informal promise.
"""
from pathlib import Path
import sys

repo = Path(__file__).resolve().parents[2]
for forbidden in ("app", "src", "n0te2"):
    path = repo / forbidden
    if path.exists() and any(path.rglob("*")):
        print(f"BOOT-02 SMOKE: RED: product implementation appeared early: {forbidden}/", file=sys.stderr)
        raise SystemExit(1)
print("BOOT-02 SMOKE: GREEN: governance-only repository surface")
