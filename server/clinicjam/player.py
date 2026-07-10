"""Playback orchestration: decides what plays next and makes it happen.

The loop owns the lifecycle of a jam:

  admin start -> ensure casting -> pop queue (or fallback playlist)
  -> tell Spotify to play the track on the librespot device
  -> wait for end-of-track (librespot event hook, API polling as backstop)
  -> repeat

librespot's --onevent hook POSTs player events to the web server, which
forwards them here via notify_librespot_event(); that gives near-instant
track advancement without hammering the Spotify API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time

from .cast import CastController
from .config import StateStore
from .jam import Jam
from .models import QueueItem, Track
from .spotify import SpotifyClient, SpotifyError, SpotifyNotConnected

log = logging.getLogger(__name__)

POLL_SECONDS = 5.0
CAST_CHECK_SECONDS = 20.0


class Player:
    def __init__(
        self,
        jam: Jam,
        spotify: SpotifyClient,
        cast: CastController,
        state: StateStore,
    ):
        self.jam = jam
        self.spotify = spotify
        self.cast = cast
        self.state = state
        self.on_change = None  # set by main.py -> schedules a WS broadcast
        self._wake = asyncio.Event()
        self._track_ended = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._device_id: str = ""
        self._fallback_pool: list[Track] = []
        self._fallback_source: str = ""
        self.last_error: str = ""

    # ---------------- lifecycle ----------------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="player-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def wake(self) -> None:
        """Something changed (song added, jam started) — re-evaluate now."""
        self._wake.set()

    def notify_librespot_event(self, event: str) -> None:
        # librespot fires 'end_of_track' when a song finishes naturally and
        # 'stopped' when playback halts entirely.
        if event in ("end_of_track", "stopped"):
            self._track_ended.set()
        self._wake.set()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    # ---------------- controls (called from the web layer) ----------------

    async def start_jam(self) -> None:
        self.jam.active = True
        self.last_error = ""
        self.wake()
        self._changed()

    async def stop_jam(self) -> None:
        self.jam.active = False
        self.jam.finish_track()
        with contextlib.suppress(SpotifyError):
            await self.spotify.pause()
        await self.cast.stop_casting()
        self.wake()
        self._changed()

    async def skip(self) -> None:
        self._track_ended.set()
        self.wake()

    async def pause(self) -> None:
        await self.spotify.pause()
        self.jam.paused = True
        self._changed()

    async def resume(self) -> None:
        await self.spotify.resume()
        self.jam.paused = False
        self._changed()

    # ---------------- fallback playlist ----------------

    async def _next_fallback(self) -> Track | None:
        source = self.state.settings.get("fallback_playlist", "")
        if not source:
            return None
        if source != self._fallback_source or not self._fallback_pool:
            try:
                tracks = await self.spotify.playlist_tracks(source)
            except SpotifyError as e:
                log.warning("fallback playlist unavailable: %s", e)
                return None
            if self.state.settings.get("explicit_filter"):
                tracks = [t for t in tracks if not t.explicit]
            random.shuffle(tracks)
            self._fallback_pool = tracks
            self._fallback_source = source
        return self._fallback_pool.pop() if self._fallback_pool else None

    # ---------------- main loop ----------------

    async def _run(self) -> None:
        last_cast_check = 0.0
        while True:
            try:
                if not self.jam.active:
                    await self._sleep(2.0)
                    continue

                # Keep the speakers latched onto our live stream.
                if time.monotonic() - last_cast_check > CAST_CHECK_SECONDS:
                    last_cast_check = time.monotonic()
                    asyncio.create_task(self.cast.ensure_casting())

                if self.jam.now_playing is None:
                    await self._play_next()
                else:
                    await self._watch_current()
            except asyncio.CancelledError:
                raise
            except SpotifyNotConnected:
                self.last_error = "Spotify account not connected (see admin panel)."
                await self._sleep(5.0)
            except Exception as e:
                log.exception("player loop error")
                self.last_error = str(e)[:200]
                await self._sleep(5.0)

    async def _sleep(self, seconds: float) -> None:
        self._wake.clear()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)

    async def _play_next(self) -> None:
        item = self.jam.pop_next()
        if item is None:
            track = await self._next_fallback()
            if track is None:
                await self._sleep(3.0)
                return
            item = QueueItem(track=track, device_id="", device_name="office playlist")

        if not self._device_id:
            self._device_id = await self.spotify.find_device() or ""
        if not self._device_id:
            # librespot not visible yet — put the song back and retry.
            if item.device_id:
                self.jam.queue.insert(0, item)
            self.last_error = (
                "Spotify Connect device not found — is the audio service running?"
            )
            await self._sleep(5.0)
            return

        self._track_ended.clear()
        try:
            await self.spotify.play(item.track.uri, self._device_id)
        except SpotifyError as e:
            # Stale device id is the common failure; rediscover once.
            log.warning("play failed (%s); rediscovering device", e)
            self._device_id = await self.spotify.find_device() or ""
            if self._device_id:
                await self.spotify.play(item.track.uri, self._device_id)
            else:
                raise
        self.jam.start_track(item)
        self.last_error = ""
        log.info("now playing: %s — %s", item.track.name, item.track.artists)
        self._changed()

    async def _watch_current(self) -> None:
        """Wait for the current track to end (event-driven, API backstop)."""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._track_ended.wait(), timeout=POLL_SECONDS)

        if self._track_ended.is_set():
            self.jam.finish_track()
            self._changed()
            return

        # Backstop: if our progress estimate says the song should be over,
        # confirm with the API (protects against a missed librespot event).
        now = self.jam.now_playing
        if now is None:
            return
        elapsed_ms = (time.time() - self.jam.playing_since) * 1000
        if elapsed_ms < now.track.duration_ms + 2000:
            return
        try:
            playback = await self.spotify.playback_state()
        except SpotifyError:
            return
        still_ours = (
            playback
            and playback.get("is_playing")
            and (playback.get("item") or {}).get("uri") == now.track.uri
        )
        if not still_ours:
            self.jam.finish_track()
            self._changed()
