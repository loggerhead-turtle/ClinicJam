"""Chromecast control for the Google Nest speakers.

pychromecast is thread-based; every blocking call is pushed through
asyncio.to_thread so the event loop stays responsive. The admin selects a
cast target — ideally a speaker group containing all the Nests, created
once in the Google Home app, which gives perfectly synced multi-room
audio for free — and we cast the Pi's local live MP3 stream to it.

Object lifecycle matters on a small box: a Chromecast object owns a
socket-client thread once connected. We create exactly one per device
uuid, reuse it across discovery updates, and disconnect it when the
device disappears or the service stops.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)

try:
    import pychromecast
    from pychromecast.controllers.media import MediaController  # noqa: F401
    HAVE_PYCHROMECAST = True
except ImportError:  # keeps dev machines without the dep importable
    HAVE_PYCHROMECAST = False


class CastController:
    def __init__(self, stream_url: str):
        self.stream_url = stream_url
        self._browser = None
        self._casts: dict[str, Any] = {}  # uuid -> Chromecast
        self._target_uuid: str = ""
        self._lock = threading.Lock()
        # Called from the discovery thread with (uuid, host) when a cast
        # device appears; main.py uses it to auto-allowlist speakers.
        self.on_device_seen: Callable[[str, str, str], None] | None = None

    # ---------------- discovery ----------------

    async def start_discovery(self) -> None:
        if not HAVE_PYCHROMECAST:
            log.warning("pychromecast not installed; cast control disabled")
            return
        await asyncio.to_thread(self._start_discovery_blocking)

    async def stop(self) -> None:
        if not HAVE_PYCHROMECAST:
            return
        await asyncio.to_thread(self._stop_blocking)

    def _stop_blocking(self) -> None:
        with self._lock:
            casts = list(self._casts.values())
            self._casts.clear()
        for cast in casts:
            try:
                cast.disconnect(timeout=2)
            except Exception:
                pass
        if self._browser:
            try:
                self._browser.stop_discovery()
            except Exception:
                pass
            self._browser = None

    def _start_discovery_blocking(self) -> None:
        def add_cast(uuid, _service):
            key = str(uuid)
            info = self._browser.devices.get(uuid)
            with self._lock:
                known = key in self._casts
            if not known and info is not None:
                try:
                    cast = pychromecast.get_chromecast_from_cast_info(info, self._browser.zc)
                except Exception:
                    log.exception("could not create cast object for %s", key)
                    return
                with self._lock:
                    if key in self._casts:
                        return
                    self._casts[key] = cast
            if info is not None and self.on_device_seen:
                try:
                    self.on_device_seen(key, str(getattr(info, "host", "") or ""),
                                        str(getattr(info, "friendly_name", "") or ""))
                except Exception:
                    log.exception("on_device_seen failed")

        def update_cast(uuid, _service):
            add_cast(uuid, _service)

        def remove_cast(uuid, _service, _cast_info):
            with self._lock:
                cast = self._casts.pop(str(uuid), None)
            if cast is not None:
                try:
                    cast.disconnect(timeout=2)
                except Exception:
                    pass

        listener = pychromecast.discovery.SimpleCastListener(add_cast, remove_cast, update_cast)
        self._browser = pychromecast.discovery.CastBrowser(listener, zeroconf_instance=None)
        self._browser.start_discovery()

    def devices(self) -> list[dict[str, Any]]:
        if not self._browser:
            return []
        out = []
        for uuid, info in dict(self._browser.devices).items():
            out.append(
                {
                    "uuid": str(uuid),
                    "name": info.friendly_name,
                    "model": info.model_name,
                    "host": str(getattr(info, "host", "") or ""),
                    "is_group": info.cast_type == "group",
                    "selected": str(uuid) == self._target_uuid,
                }
            )
        return sorted(out, key=lambda d: (not d["is_group"], d["name"]))

    def device_hosts(self) -> set[str]:
        return {d["host"] for d in self.devices() if d["host"]}

    # ---------------- target + playback ----------------

    def set_target(self, uuid: str) -> None:
        self._target_uuid = uuid

    def _target_cast(self):
        with self._lock:
            return self._casts.get(self._target_uuid)

    async def ensure_casting(self) -> bool:
        """Make sure the target is playing our live stream; True if healthy."""
        cast = self._target_cast()
        if cast is None:
            return False
        return await asyncio.to_thread(self._ensure_casting_blocking, cast)

    def _ensure_casting_blocking(self, cast) -> bool:
        try:
            cast.wait(timeout=10)
            mc = cast.media_controller
            playing_ours = (
                mc.status is not None
                and mc.status.content_id == self.stream_url
                and mc.status.player_state in ("PLAYING", "BUFFERING")
            )
            if not playing_ours:
                log.info("casting %s to %s", self.stream_url, cast.name)
                mc.play_media(
                    self.stream_url,
                    "audio/mpeg",
                    stream_type="LIVE",
                    title="ClinicJam",
                )
                mc.block_until_active(timeout=10)
            return True
        except Exception:
            log.exception("ensure_casting failed for %s", getattr(cast, "name", "?"))
            return False

    async def stop_casting(self) -> None:
        cast = self._target_cast()
        if cast is None:
            return

        def _stop():
            try:
                cast.wait(timeout=5)
                cast.quit_app()
            except Exception:
                log.exception("stop_casting failed")

        await asyncio.to_thread(_stop)

    # ---------------- volume ----------------

    async def set_volume(self, level: float, uuid: str = "") -> bool:
        """Set volume 0.0-1.0 on a specific device, or the target if blank."""
        with self._lock:
            cast = self._casts.get(uuid) if uuid else self._casts.get(self._target_uuid)
        if cast is None:
            return False

        def _set():
            try:
                cast.wait(timeout=5)
                cast.set_volume(max(0.0, min(1.0, level)))
                return True
            except Exception:
                log.exception("set_volume failed")
                return False

        return await asyncio.to_thread(_set)

    async def status(self) -> dict[str, Any]:
        cast = self._target_cast()
        if cast is None:
            return {"connected": False}

        def _status():
            try:
                cast.wait(timeout=5)
                mc = cast.media_controller
                return {
                    "connected": True,
                    "name": cast.name,
                    "volume": round(cast.status.volume_level, 2) if cast.status else None,
                    "player_state": mc.status.player_state if mc.status else "UNKNOWN",
                    "casting_ours": bool(
                        mc.status and mc.status.content_id == self.stream_url
                    ),
                }
            except Exception:
                return {"connected": False}

        return await asyncio.to_thread(_status)
