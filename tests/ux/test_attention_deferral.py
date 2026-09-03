from __future__ import annotations

import json

import pytest

from n0te2.lineage import ValidationError
from n0te2.memory import HeadquartersMemory

ITEM = "SUGGESTION:arrangement:contrast-window"


def test_all_not_now_horizons_are_durable_bounded_and_reversible(tmp_path) -> None:
    root = tmp_path.resolve()
    hq = HeadquartersMemory.create(root, "Attention Artist")
    profile_id = hq.store.profile_id
    song = hq.store.create_song("Current Song")
    session = hq.sessions.start_session(song_id=song.id, objective="Finish the hook")
    next_song = hq.store.create_song("Next Song")
    hq.store.select_song(song.id)

    later = hq.attention_deferrals.defer(
        ITEM,
        "LATER_THIS_SONG",
        song_id=song.id,
        anchor=f"session:{session.id}",
    )
    assert hq.attention_deferrals.applies(
        ITEM, song_id=song.id, anchor=f"session:{session.id}"
    )
    assert not hq.attention_deferrals.applies(
        ITEM, song_id=song.id, anchor="session:changed"
    )
    same = hq.attention_deferrals.defer(
        ITEM,
        "LATER_THIS_SONG",
        song_id=song.id,
        anchor=f"session:{session.id}",
    )
    assert same == later

    next_horizon = hq.attention_deferrals.defer(ITEM, "NEXT_SONG", song_id=song.id)
    assert next_horizon.id != later.id
    assert hq.attention_deferrals.applies(ITEM, song_id=song.id)
    assert not hq.attention_deferrals.applies(ITEM, song_id=next_song.id)

    release_horizon = hq.attention_deferrals.defer(ITEM, "AFTER_RELEASE", song_id=song.id)
    assert hq.attention_deferrals.applies(ITEM, song_id=song.id)
    assert not hq.attention_deferrals.applies(
        ITEM, song_id=song.id, released_song_ids={song.id}
    )
    assert not hq.attention_deferrals.applies(ITEM, song_id=next_song.id)

    someday = hq.attention_deferrals.defer(ITEM, "SOMEDAY")
    assert someday.song_id is None and someday.anchor is None
    assert hq.attention_deferrals.applies(ITEM, song_id=next_song.id)

    never = hq.attention_deferrals.defer(ITEM, "NEVER_SUGGEST_AGAIN")
    assert never.song_id is None and never.anchor is None
    assert hq.attention_deferrals.applies(ITEM, song_id=song.id)

    history = hq.attention_deferrals.history(ITEM)
    assert len(history) == 5
    assert [item.horizon for item in history] == [
        "LATER_THIS_SONG",
        "NEXT_SONG",
        "AFTER_RELEASE",
        "SOMEDAY",
        "NEVER_SUGGEST_AGAIN",
    ]
    assert [item.clear_reason for item in history[:-1]] == ["SUPERSEDED"] * 4
    assert history[-1].state == "ACTIVE"

    hq.close()
    reopened = HeadquartersMemory.open(root, profile_id)
    try:
        assert reopened.attention_deferrals.active(ITEM).id == never.id
        restored = reopened.attention_deferrals.restore(ITEM)
        assert restored is not None
        assert restored.state == "CLEARED"
        assert restored.clear_reason == "RESTORED"
        assert reopened.attention_deferrals.active(ITEM) is None
        assert not reopened.attention_deferrals.applies(ITEM, song_id=song.id)
    finally:
        reopened.close()


def test_deferrals_are_profile_isolated_and_item_keys_are_semantic(tmp_path) -> None:
    root = tmp_path.resolve()
    first = HeadquartersMemory.create(root, "First Artist")
    first_id = first.store.profile_id
    song = first.store.create_song("One")
    first.attention_deferrals.defer(ITEM, "NEXT_SONG", song_id=song.id)
    first.close()

    second = HeadquartersMemory.create(root, "Second Artist")
    try:
        assert second.attention_deferrals.active(ITEM) is None
        with pytest.raises(ValidationError):
            second.attention_deferrals.defer("NOW_THREAD", "SOMEDAY")
        with pytest.raises(ValidationError):
            second.attention_deferrals.defer("SUGGESTION:unknown:key", "SOMEDAY")
    finally:
        second.close()

    reopened = HeadquartersMemory.open(root, first_id)
    try:
        assert reopened.attention_deferrals.active(ITEM) is not None
    finally:
        reopened.close()


def test_deferral_activity_is_auditable_without_becoming_the_item_store(tmp_path) -> None:
    hq = HeadquartersMemory.create(tmp_path.resolve(), "Audit Artist")
    try:
        song = hq.store.create_song("Audit Song")
        before = hq.activity.checkpoint()
        deferred = hq.attention_deferrals.defer(ITEM, "NEXT_SONG", song_id=song.id)
        hq.attention_deferrals.restore(ITEM)

        rows = hq.store._conn.execute(
            "SELECT event_type,object_type,object_id,payload_json "
            "FROM activity_events WHERE seq>? ORDER BY seq",
            (before,),
        ).fetchall()
        events = [str(row["event_type"]) for row in rows]
        assert events == ["ATTENTION_DEFERRED", "ATTENTION_DEFERRAL_CLEARED"]
        assert all(str(row["object_type"]) == "ATTENTION_DEFERRAL" for row in rows)
        assert all(str(row["object_id"]) == deferred.id for row in rows)
        payloads = [json.loads(str(row["payload_json"])) for row in rows]
        assert payloads[0] == {"item_key": ITEM, "horizon": "NEXT_SONG"}
        assert payloads[1] == {"item_key": ITEM, "reason": "RESTORED"}
    finally:
        hq.close()
