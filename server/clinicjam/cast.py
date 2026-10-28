"""Chromecast control for the Google Nest speakers.

pychromecast is thread-based; every blocking call is pushed through
asyncio.to_thread so the event loop stays responsive. The admin selects a
cast target — ideally a speaker group containing all five Nests, created
once in the Google Home app, which gives perfectly synced multi-room
audio for free — and we cast the Pi's local live MP3 stream to it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

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

    # ---------------- discovery ----------------

    async def start_discovery(self) -> None:
        if not HAVE_PYCHROMECAST:
            log.warning("pychromecast not installed; cast control disabled")
            return
        await asyncio.to_thread(self._start_discovery_blocking)

    def _start_discovery_blocking(self) -> None:
        def add_cast(uuid, _service):
            cast = pychromecast.get_chromecast_from_cast_info(
                self._browser.devices[uuid], self._browser.zc
            )
            with self._lock:
                self._casts[str(uuid)] = cast

        def remove_cast(uuid, _service, _cast_info):
            with self._lock:
                self._casts.pop(str(uuid), None)

        listener = pychromecast.discovery.SimpleCastListener(add_cast, remove_cast)
        self._browser = pychromecast.discovery.CastBrowser(
            listener, zeroconf_instance=None
        )
        self._browser.start_discovery()

    async def stop_discovery(self) -> None:
        """Tear down the zeroconf browser thread on shutdown. Systemd kills
        the whole process on a service restart regardless, but this keeps
        the app well-behaved if it's ever run outside systemd (dev, tests)."""
        if self._browser is not None:
            await asyncio.to_thread(self._browser.stop_discovery)
            self._browser = None

    def devices(self) -> list[dict[str, Any]]:
        if not self._browser:
            return []
        out = []
        for uuid, info in self._browser.devices.items():
            out.append(
                {
                    "uuid": str(uuid),
                    "name": info.friendly_name,
                    "model": info.model_name,
                    "is_group": info.cast_type == "group",
                    "selected": str(uuid) == self._target_uuid,
                }
            )
        return sorted(out, key=lambda d: (not d["is_group"], d["name"]))

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
