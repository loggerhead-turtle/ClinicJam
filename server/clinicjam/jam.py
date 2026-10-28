"""The jam itself: the shared queue, votes, skip votes, and guest devices.

Pure in-memory logic with no I/O so it is easy to test. The player loop
consumes items via pop_next(); the web layer mutates and renders it.
"""

from __future__ import annotations

import math
import time
from typing import Any

from .models import QueueItem, Track

ACTIVE_WINDOW_SECONDS = 10 * 60


class JamError(Exception):
    """User-facing rule violation (duplicate, banned, limit reached...)."""


class Jam:
    def __init__(self, settings: dict[str, Any]):
        # `settings` is the live dict from StateStore — admin changes apply
        # immediately without re-wiring.
        self.settings = settings
        self.queue: list[QueueItem] = []
        self.now_playing: QueueItem | None = None
        self.playing_since: float = 0.0
        self.paused: bool = False
        self.skip_votes: set[str] = set()
        self.devices: dict[str, dict[str, Any]] = {}  # device_id -> {name, last_seen}
        self.banned: set[str] = set()
        self.active: bool = False  # admin started the jam
        self.history: list[Track] = []

    # ---------------- devices ----------------

    def touch_device(self, device_id: str, name: str = "") -> None:
        if not device_id:
            return
        entry = self.devices.setdefault(device_id, {"name": "", "last_seen": 0.0})
        entry["last_seen"] = time.time()
        if name:
            entry["name"] = name[:24]

    def active_device_count(self) -> int:
        cutoff = time.time() - ACTIVE_WINDOW_SECONDS
        return sum(1 for d in self.devices.values() if d["last_seen"] >= cutoff)

    def prune_stale_devices(self, max_age_seconds: float = 2 * 24 * 3600) -> int:
        """Drop devices not seen in a while so `self.devices` doesn't grow
        forever over a months-long deployment (every phone that's ever
        joined would otherwise sit in memory permanently). Bans survive —
        they're keyed by device id in a separate set, not here. Returns the
        number of entries removed."""
        cutoff = time.time() - max_age_seconds
        stale = [did for did, info in self.devices.items() if info["last_seen"] < cutoff]
        for did in stale:
            del self.devices[did]
        return len(stale)

    def _check_allowed(self, device_id: str) -> None:
        if device_id in self.banned:
            raise JamError("This device has been blocked by the host.")

    # ---------------- queue ----------------

    def add(self, track: Track, device_id: str, device_name: str) -> QueueItem:
        self._check_allowed(device_id)
        if self.settings.get("explicit_filter") and track.explicit:
            raise JamError("Explicit tracks are turned off for this jam.")
        if any(item.track.uri == track.uri for item in self.queue):
            raise JamError("That song is already in the queue.")
        if self.now_playing and self.now_playing.track.uri == track.uri:
            raise JamError("That song is playing right now.")
        limit = int(self.settings.get("per_user_limit") or 0)
        if limit > 0:
            pending = sum(1 for item in self.queue if item.device_id == device_id)
            if pending >= limit:
                raise JamError(f"You already have {limit} songs waiting — vote instead!")
        item = QueueItem(track=track, device_id=device_id, device_name=device_name)
        # The person who queues a song implicitly wants to hear it.
        item.votes.add(device_id)
        self.queue.append(item)
        return item

    def remove(self, item_id: str) -> bool:
        before = len(self.queue)
        self.queue = [i for i in self.queue if i.id != item_id]
        return len(self.queue) != before

    def ordered_queue(self) -> list[QueueItem]:
        if self.settings.get("queue_mode") == "fifo":
            return sorted(self.queue, key=lambda i: i.added_at)
        return sorted(self.queue, key=lambda i: (-len(i.votes), i.added_at))

    def pop_next(self) -> QueueItem | None:
        ordered = self.ordered_queue()
        if not ordered:
            return None
        item = ordered[0]
        self.queue.remove(item)
        return item

    def start_track(self, item: QueueItem) -> None:
        if self.now_playing:
            self.history.append(self.now_playing.track)
            self.history = self.history[-50:]
        self.now_playing = item
        self.playing_since = time.time()
        self.paused = False
        self.skip_votes.clear()

    def finish_track(self) -> None:
        if self.now_playing:
            self.history.append(self.now_playing.track)
            self.history = self.history[-50:]
        self.now_playing = None
        self.skip_votes.clear()

    # ---------------- votes ----------------

    def vote(self, item_id: str, device_id: str, up: bool) -> None:
        self._check_allowed(device_id)
        for item in self.queue:
            if item.id == item_id:
                if up:
                    item.votes.add(device_id)
                else:
                    item.votes.discard(device_id)
                return
        raise JamError("That song is no longer in the queue.")

    def skip_threshold(self) -> int:
        ratio = float(self.settings.get("skip_ratio") or 0.5)
        return max(2, math.ceil(self.active_device_count() * ratio))

    def vote_skip(self, device_id: str) -> bool:
        """Record a skip vote; returns True when the threshold is reached."""
        self._check_allowed(device_id)
        if not self.now_playing:
            raise JamError("Nothing is playing.")
        self.skip_votes.add(device_id)
        return len(self.skip_votes) >= self.skip_threshold()

    # ---------------- rendering ----------------

    def public_state(self, viewer_device: str = "") -> dict[str, Any]:
        now = self.now_playing
        progress = 0
        if now:
            progress = int((time.time() - self.playing_since) * 1000)
            progress = min(progress, now.track.duration_ms)
        return {
            "active": self.active,
            "paused": self.paused,
            "now_playing": now.public(viewer_device) if now else None,
            "progress_ms": progress if now else 0,
            "queue": [i.public(viewer_device) for i in self.ordered_queue()],
            "queue_mode": self.settings.get("queue_mode", "vote"),
            "explicit_filter": bool(self.settings.get("explicit_filter", True)),
            "listeners": self.active_device_count(),
            "skip_votes": len(self.skip_votes),
            "skip_threshold": self.skip_threshold(),
            "you_voted_skip": viewer_device in self.skip_votes,
        }
