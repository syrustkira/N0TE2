from __future__ import annotations

import sqlite3

import pytest

from n0te2.lineage import NotFoundError
from n0te2.memory import HeadquartersMemory


ITEM = "NOW_THREAD"


def test_all_not_now_horizons_are_durable_bounded_and_reversible(tmp_path) -> None:
    root = tmp_path.resolve()
    hq = HeadquartersMemory.create(root, "Attention Artist")
    profile_id = hq.store.profile_id
    song = hq.store.create_song("Current Song")
    next_song = hq.store.create_song("Next Song")

    later = hq.attention_deferrals.defer(
        ITEM,
        "LATER_THIS_SONG",
        song_id=song.id,
        anchor="thread:v1",
    )
    assert hq.attention_deferrals.applies(
        ITEM, song_id=song.id, anchor="thread:v1"
    )
    assert not hq.attention_deferrals.applies(
        ITEM, song_id=song.id, anchor="thread:v2"
    )

    same = hq.attention_deferrals.defer(
        ITEM,
        "LATER_THIS_SONG",
        song_id=song.id,
        anchor="thread:v1",
    )
    assert same == later

    current_song = hq.attention_deferrals.defer(
        ITEM, "NEXT_SONG", song_id=song.id
    )
    assert current_song.id != later.id
    assert hq.attention_deferrals.applies(ITEM, song_id=song.id)
    assert not hq.attention_deferrals.applies(ITEM, song_id=next_song.id)

    after_release = hq.attention_deferrals.defer(
        ITEM, "AFTER_RELEASE", song_id=song.id
    )
    assert hq.attention_deferrals.applies(ITEM, song_id=song.id)
    assert not hq.attention_deferrals.applies(
        ITEM, song_id=song.id, released_song_ids={song.id}
    )
    assert not hq.attention_deferrals.applies(ITEM, song_id=next_song.id)

    someday = hq.attention_deferrals.defer(ITEM, "SOMEDAY")
    assert someday.song_id is None
    assert hq.attention_deferrals.applies(ITEM, song_id=next_song.id)

    never = hq.attention_deferrals.defer(ITEM, "NEVER_SUGGEST_AGAIN")
    assert never.song_id is None
    assert hq.attention_deferrals.applies(ITEM, song_id=None)

    restored = hq.attention_deferrals.restore(ITEM)
    assert restored is not None
    assert restored.id == never.id
    assert restored.state == "CLEARED"
    assert restored.clear_reason == "RESTORED"
    assert not hq.attention_deferrals.applies(ITEM, song_id=song.id)

    history = hq.attention_deferrals.history(ITEM)
    assert [row.horizon for row in history] == [
        "LATER_THIS_SONG",
        "NEXT_SONG",
        "AFTER_RELEASE",
        "SOMEDAY",
        "NEVER_SUGGEST_AGAIN",
    ]
    assert [row.clear_reason for row in history[:-1]] == [
        "SUPERSEDED",
        "SUPERSEDED",
        "SUPERSEDED",
        "SUPERSEDED",
    ]
    hq.close()

    with HeadquartersMemory.open(root, profile_id) as reopened:
        persisted = reopened.attention_deferrals.history(ITEM)
        assert persisted == history
        assert reopened.attention_deferrals.active(ITEM) is None


def test_deferral_cannot_cross_artist_profile_and_does_not_create_taste_evidence(tmp_path) -> None:
    root = tmp_path.resolve()
    first = HeadquartersMemory.create(root, "First Artist")
    foreign_song = first.store.create_song("Foreign Song")
    first.close()

    second = HeadquartersMemory.create(root, "Second Artist")
    try:
        before_claims = second.store._conn.execute(
            "SELECT COUNT(*) AS n FROM evidence_claims"
        ).fetchone()["n"]
        with pytest.raises(NotFoundError):
            second.attention_deferrals.defer(
                ITEM,
                "NEXT_SONG",
                song_id=foreign_song.id,
            )
        assert second.attention_deferrals.history() == ()
        after_claims = second.store._conn.execute(
            "SELECT COUNT(*) AS n FROM evidence_claims"
        ).fetchone()["n"]
        assert after_claims == before_claims
    finally:
        second.close()


def test_deferral_history_is_append_only_except_bounded_clear_transition(tmp_path) -> None:
    with HeadquartersMemory.create(tmp_path.resolve(), "Tamper Artist") as hq:
        row = hq.attention_deferrals.defer(ITEM, "SOMEDAY")
        conn = hq.store._conn
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "UPDATE attention_deferrals SET horizon='NEXT_SONG' WHERE id=?",
                (row.id,),
            )
        conn.rollback()
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM attention_deferrals WHERE id=?", (row.id,))
        conn.rollback()
        assert hq.attention_deferrals.active(ITEM) == row
