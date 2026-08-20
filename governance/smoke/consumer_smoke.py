#!/usr/bin/env python3
"""Stage-aware construction smoke for the active bounded consumer outcome."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))

state = json.loads((repo / "governance/current_state.json").read_text())
active = state["active_node"]
increment = state.get("active_increment")

if active in {"BOOT-02", "LEGACY-01"}:
    for forbidden in ("app", "src", "n0te2", "legacy"):
        path = repo / forbidden
        if path.exists() and any(path.rglob("*")):
            print(
                f"PRE-PRODUCT SMOKE: RED: product implementation appeared early: {forbidden}/",
                file=sys.stderr,
            )
            raise SystemExit(1)
    print("PRE-PRODUCT SMOKE: GREEN")
    raise SystemExit(0)

if active != "CORE-01" or increment != "CORE-01J":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2.lineage import LineageStore  # noqa: E402


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    store = LineageStore.create(root, "Crash Smoke Artist")
    profile = store.profile_id
    committed = store.create_song("Committed Song")
    committed_count = store._conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    store.close()

    child = textwrap.dedent(
        """
        import os
        import sys
        from n0te2.lineage import LineageStore

        root, profile = sys.argv[1], sys.argv[2]
        store = LineageStore.open(root, profile)
        store._conn.execute('BEGIN IMMEDIATE')
        store._conn.execute(
            'INSERT INTO songs(id,artist_id,title) VALUES(?,?,?)',
            ('song_' + 'f' * 32, store.primary_artist_id, 'UNCOMMITTED')
        )
        store._conn.execute(
            "UPDATE metadata SET value=? WHERE key='active_song_id'",
            ('song_' + 'f' * 32,)
        )
        os._exit(23)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", child, str(root), profile],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(repo)},
        check=False,
    )
    assert result.returncode == 23

    reopened = LineageStore.open(root, profile)
    assert reopened._conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == committed_count
    assert reopened.get_song("song_" + "f" * 32) is None
    assert reopened.active_song().id == committed.id
    assert reopened._conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    reopened.close()

print(
    "CORE-01J CONSUMER SMOKE: GREEN: killed uncommitted Song mutation rolled back; prior canonical Artist/Song state reopened intact"
)
