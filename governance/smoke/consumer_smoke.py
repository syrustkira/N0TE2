#!/usr/bin/env python3
"""Stage-aware construction smoke for the active bounded consumer outcome."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
import sys

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

if active != "CORE-01" or increment != "CORE-01I":
    print(
        f"STAGE SMOKE: RED: unsupported active stage {active}/{increment}",
        file=sys.stderr,
    )
    raise SystemExit(1)

from n0te2 import HeadquartersMemory  # noqa: E402


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


with tempfile.TemporaryDirectory() as td:
    root = Path(td)

    a = HeadquartersMemory.create(root, "Smoke Artist A")
    profile_a = a.store.profile_id
    song_a = a.store.create_song("A Song")
    artist_claim_a = a.evidence.record_claim(
        scope_kind="ARTIST",
        scope_id=a.store.primary_artist_id,
        key="artist.private",
        value="A only",
        source_kind="USER_DECLARED",
        twin_domain="CREATIVE",
    )
    song_claim_a = a.evidence.record_claim(
        scope_kind="SONG",
        scope_id=song_a.id,
        key="song.private",
        value="A Song only",
        source_kind="USER_DECLARED",
        twin_domain="CREATIVE",
    )
    product_fingerprint = a.context.product.fingerprint
    a_db = a.store.database_path
    a.close()
    a_hash = sha256_file(a_db)

    b = HeadquartersMemory.create(root, "Smoke Artist B")
    profile_b = b.store.profile_id
    song_b1 = b.store.create_song("B Song 1")
    song_b2 = b.store.create_song("B Song 2")
    evidence_before = int(
        b.store._conn.execute(
            "SELECT COUNT(*) AS n FROM evidence_claims"
        ).fetchone()["n"]
    )

    imported_artist = b.context.import_context(
        scope_kind="ARTIST",
        scope_id=b.store.primary_artist_id,
        source_kind="IMPORTED",
        source_ref="profile-a-export",
        payload={
            "artist_name": "Smoke Artist A",
            "artist_id": artist_claim_a.scope_id,
            "product_context": {
                "primary_object": "DAW",
                "artist_authority": "IMPORT",
            },
            "authority": "MUTATE",
            "song_id": song_a.id,
        },
    )
    imported_song = b.context.import_context(
        scope_kind="SONG",
        scope_id=song_b1.id,
        source_kind="SYNCED",
        source_ref="song-sync",
        payload={"note": "B1 only"},
    )

    conn = b.store._conn
    tables = (
        "metadata",
        "songs",
        "versions",
        "assets",
        "evidence_claims",
        "evidence_supersessions",
        "activity_events",
        "provenance_records",
        "context_imports",
    )
    before = {
        table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        for table in tables
    }
    before_changes = conn.total_changes

    env_b1 = b.context.envelope(song_id=song_b1.id)
    repeated = b.context.envelope(song_id=song_b1.id)
    env_b2 = b.context.envelope(song_id=song_b2.id)

    after = {
        table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        for table in tables
    }

    assert env_b1 == repeated
    assert before == after and before_changes == conn.total_changes
    assert env_b1.product.fingerprint == product_fingerprint
    assert env_b1.artist_name == "Smoke Artist B"
    assert env_b1.profile_id == profile_b and env_b1.profile_id != profile_a
    assert imported_artist.authority == "EVIDENCE_ONLY"
    assert imported_song.authority == "EVIDENCE_ONLY"
    assert imported_artist in env_b1.imports
    assert imported_song in env_b1.imports
    assert imported_artist in env_b2.imports
    assert imported_song not in env_b2.imports
    assert int(
        conn.execute("SELECT COUNT(*) AS n FROM evidence_claims").fetchone()["n"]
    ) == evidence_before
    assert sha256_file(a_db) == a_hash
    assert artist_claim_a.id not in {claim.id for claim in env_b1.artist_claims}
    assert song_claim_a.id not in {claim.id for claim in env_b1.song_claims}
    b.close()

    a = HeadquartersMemory.open(root, profile_a)
    env_a = a.context.envelope(song_id=song_a.id)
    assert env_a.product.fingerprint == product_fingerprint
    assert env_a.artist_name == "Smoke Artist A"
    assert artist_claim_a.id in {claim.id for claim in env_a.artist_claims}
    assert song_claim_a.id in {claim.id for claim in env_a.song_claims}
    assert not env_a.imports
    a.close()

print(
    "CORE-01I CONSUMER SMOKE: GREEN: fresh profiles share generic ProductContext but not private Artist/Song context; imports stay EVIDENCE_ONLY and cannot replace doctrine or authority"
)
