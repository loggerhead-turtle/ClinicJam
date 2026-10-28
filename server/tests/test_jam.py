import time

import pytest

from clinicjam.jam import Jam, JamError
from clinicjam.models import Track


def make_track(uri="spotify:track:abc", explicit=False, name="Song"):
    return Track(
        uri=uri, name=name, artists="Artist", album="Album",
        art_url="", duration_ms=200_000, explicit=explicit,
    )


def make_jam(**settings):
    base = {
        "queue_mode": "vote",
        "explicit_filter": True,
        "skip_ratio": 0.5,
        "per_user_limit": 0,
        "fallback_playlist": "",
    }
    base.update(settings)
    return Jam(base)


def test_add_and_pop_fifo_order():
    jam = make_jam(queue_mode="fifo")
    a = jam.add(make_track("spotify:track:a"), "dev1", "Ann")
    jam.add(make_track("spotify:track:b"), "dev2", "Bob")
    assert jam.pop_next().id == a.id


def test_vote_mode_orders_by_votes_then_age():
    jam = make_jam()
    jam.add(make_track("spotify:track:a"), "dev1", "Ann")
    b = jam.add(make_track("spotify:track:b"), "dev2", "Bob")
    jam.vote(b.id, "dev3", up=True)
    assert jam.pop_next().id == b.id  # 2 votes (adder + dev3) beats 1


def test_duplicate_rejected():
    jam = make_jam()
    jam.add(make_track(), "dev1", "Ann")
    with pytest.raises(JamError):
        jam.add(make_track(), "dev2", "Bob")


def test_explicit_filter_blocks():
    jam = make_jam()
    with pytest.raises(JamError):
        jam.add(make_track(explicit=True), "dev1", "Ann")
    jam.settings["explicit_filter"] = False
    jam.add(make_track(explicit=True), "dev1", "Ann")


def test_per_user_limit():
    jam = make_jam(per_user_limit=1)
    jam.add(make_track("spotify:track:a"), "dev1", "Ann")
    with pytest.raises(JamError):
        jam.add(make_track("spotify:track:b"), "dev1", "Ann")
    jam.add(make_track("spotify:track:b"), "dev2", "Bob")


def test_banned_device_blocked():
    jam = make_jam()
    jam.banned.add("dev1")
    with pytest.raises(JamError):
        jam.add(make_track(), "dev1", "Ann")


def test_skip_threshold_and_votes():
    jam = make_jam(skip_ratio=0.5)
    for i in range(4):
        jam.touch_device(f"dev{i}")
    item = jam.add(make_track(), "dev0", "Ann")
    jam.queue.remove(item)
    jam.start_track(item)
    assert jam.skip_threshold() == 2
    assert jam.vote_skip("dev1") is False
    assert jam.vote_skip("dev1") is False  # same device doesn't double-count
    assert jam.vote_skip("dev2") is True


def test_skip_votes_reset_on_new_track():
    jam = make_jam()
    jam.touch_device("dev1")
    jam.touch_device("dev2")
    a = jam.add(make_track("spotify:track:a"), "dev1", "Ann")
    jam.queue.remove(a)
    jam.start_track(a)
    jam.vote_skip("dev1")
    b = jam.add(make_track("spotify:track:b"), "dev2", "Bob")
    jam.queue.remove(b)
    jam.start_track(b)
    assert len(jam.skip_votes) == 0


def test_active_device_count_expires():
    jam = make_jam()
    jam.touch_device("old")
    jam.devices["old"]["last_seen"] = time.time() - 3600
    jam.touch_device("fresh")
    assert jam.active_device_count() == 1


def test_prune_stale_devices_removes_only_old_entries():
    jam = make_jam()
    jam.touch_device("ancient")
    jam.devices["ancient"]["last_seen"] = time.time() - 10 * 24 * 3600  # 10 days
    jam.touch_device("fresh")
    removed = jam.prune_stale_devices(max_age_seconds=2 * 24 * 3600)
    assert removed == 1
    assert "ancient" not in jam.devices
    assert "fresh" in jam.devices


def test_prune_stale_devices_keeps_bans_intact():
    # Bans live in a separate set, keyed by device id — pruning the
    # devices dict (last-seen bookkeeping) must never un-ban anyone.
    jam = make_jam()
    jam.touch_device("troublemaker")
    jam.devices["troublemaker"]["last_seen"] = time.time() - 10 * 24 * 3600
    jam.banned.add("troublemaker")
    jam.prune_stale_devices(max_age_seconds=2 * 24 * 3600)
    assert "troublemaker" in jam.banned


def test_public_state_shape():
    jam = make_jam()
    item = jam.add(make_track(), "dev1", "Ann")
    state = jam.public_state("dev1")
    assert state["queue"][0]["id"] == item.id
    assert state["queue"][0]["voted"] is True
    assert state["queue"][0]["mine"] is True
    assert state["now_playing"] is None
