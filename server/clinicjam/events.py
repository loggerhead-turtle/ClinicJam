"""WebSocket hub: pushes jam state to every connected phone.

Sends fan out concurrently with a short timeout per socket, so one phone
with a stalled connection cannot hold up the broadcast for everyone else
(it is closed and dropped instead).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Callable

from starlette.websockets import WebSocket

log = logging.getLogger(__name__)

SEND_TIMEOUT = 3.0
MAX_CLIENTS = 150


class HubFull(Exception):
    pass


class Hub:
    def __init__(self):
        self.clients: dict[WebSocket, str] = {}  # socket -> device_id

    async def connect(self, ws: WebSocket, device_id: str) -> None:
        if len(self.clients) >= MAX_CLIENTS:
            await ws.close(code=1013)  # try again later
            raise HubFull()
        await ws.accept()
        self.clients[ws] = device_id

    async def disconnect(self, ws: WebSocket) -> None:
        self.clients.pop(ws, None)

    async def _send_one(self, ws: WebSocket, payload: str) -> None:
        try:
            await asyncio.wait_for(ws.send_text(payload), SEND_TIMEOUT)
        except Exception:
            self.clients.pop(ws, None)
            with contextlib.suppress(Exception):
                await ws.close()

    async def broadcast_state(self, state_for: Callable[[str], dict[str, Any]]) -> None:
        """state_for(device_id) -> per-viewer state dict (vote flags differ)."""
        targets = list(self.clients.items())
        if not targets:
            return
        rendered: dict[str, str] = {}
        sends = []
        for ws, device_id in targets:
            payload = rendered.get(device_id)
            if payload is None:
                payload = json.dumps({"type": "state", "state": state_for(device_id)})
                rendered[device_id] = payload
            sends.append(self._send_one(ws, payload))
        await asyncio.gather(*sends)
