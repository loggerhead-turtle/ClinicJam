"""The jam itself: the shared queue, votes, skip votes, and guest devices.

Pure in-memory logic with no I/O so it is easy to test. The player loop
consumes items via pop_next(); the web layer mutates and renders it.

Every collection here is bounded: a guest sending random device ids all
day cannot grow the server's memory.
"""

from __future__ import annotations

import math
import time
from typing import Any

from .models import QueueItem, Track

ACTIVE_WINDOW_SECONDS = 10 * 60
DEVICE_FORGET_SECONDS = 24 * 3600
MAX_DEVICES = 500
MAX_QUEUE = 200


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
        self.paused_at: float = 0.0
        self.skip_votes: set[str] = set()
        self.devices: dict[str, dict[str, Any]] = {}  # device_id -> {name, last_seen, ip}
        self.banned: set[str] = set()
        self.active: bool = False  # admin started the jam
        self.history: list[Track] = []

    # ---------------- devices ----------------

    def touch_device(
        self, device_id: str, name: str = "", ip: str = "", counted: bool = True
    ) -> None:
        """Record activity. Only `counted` devices (ones that have joined with
        the PIN) count towards the listener total and skip threshold, so a
        stranger can't inflate or deflate the vote maths from outside."""
        if not device_id:
            return
        entry = self.devices.get(device_id)
        if entry is None:
            if len(self.devices) >= MAX_DEVICES:
                self.prune_devices(force=True)
            entry = self.devices[device_id] = {
                "name": "", "last_seen": 0.0, "ip": "", "counted": False
            }
        entry["last_seen"] = time.time()
        if name:
            entry["name"] = name[:24]
        if ip:
            entry["ip"] = ip
        if counted:
            entry["counted"] = True

    def prune_devices(self, force: bool = False) -> None:
        """Forget devices not seen for a day; under pressure, the oldest."""
        cutoff = time.time() - DEVICE_FORGET_SECONDS
        for did in [d for d, e in self.devices.items() if e["last_seen"] < cutoff]:
            del self.devices[did]
        if force and len(self.devices) >= MAX_DEVICES:
            oldest = sorted(self.devices, key=lambda d: self.devices[d]["last_seen"])
            for did in oldest[: len(self.devices) - MAX_DEVICES + 50]:
                del self.devices[did]

    def active_device_count(self) -> int:
        cutoff = time.time() - ACTIVE_WINDOW_SECONDS
        return sum(
            1 for d in self.devices.values()
            if d["last_seen"] >= cutoff and d.get("counted", True)
        )

    def _check_allowed(self, device_id: str) -> None:
        if device_id in self.banned:
            raise JamError("This device has been blocked by the host.")

    # ---------------- queue ----------------

    def add(self, track: Track, device_id: str, device_name: str) -> QueueItem:
        self._check_allowed(device_id)
        if self.settings.get("explicit_filter") and track.explicit:
            raise JamError("Explicit tracks are turned off for this jam.")
        if len(self.queue) >= MAX_QUEUE:
            raise JamError("The queue is full — vote on what's already there!")
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

    def remove_from_device(self, device_id: str) -> None:
        self.queue = [i for i in self.queue if i.device_id != device_id]

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
        self.paused = False
        self.skip_votes.clear()

    def set_paused(self, paused: bool) -> None:
        if paused and not self.paused:
            self.paused_at = time.time()
        elif not paused and self.paused:
            # Shift the start time so progress resumes where it stopped.
            self.playing_since += time.time() - self.paused_at
        self.paused = paused

    def progress_ms(self) -> int:
        if not self.now_playing:
            return 0
        reference = self.paused_at if self.paused else time.time()
        progress = int((reference - self.playing_since) * 1000)
        return max(0, min(progress, self.now_playing.track.duration_ms))

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
        return {
            "active": self.active,
            "paused": self.paused,
            "now_playing": now.public(viewer_device) if now else None,
            "progress_ms": self.progress_ms(),
            "queue": [i.public(viewer_device) for i in self.ordered_queue()],
            "queue_mode": self.settings.get("queue_mode", "vote"),
            "explicit_filter": bool(self.settings.get("explicit_filter", True)),
            "listeners": self.active_device_count(),
            "skip_votes": len(self.skip_votes),
            "skip_threshold": self.skip_threshold(),
            "you_voted_skip": viewer_device in self.skip_votes,
        }
