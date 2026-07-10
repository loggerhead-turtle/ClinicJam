"""WebSocket hub: pushes jam state to every connected phone."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from starlette.websockets import WebSocket

log = logging.getLogger(__name__)


class Hub:
    def __init__(self):
        self.clients: dict[WebSocket, str] = {}  # socket -> device_id
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, device_id: str) -> None:
        await ws.accept()
        async with self._lock:
            self.clients[ws] = device_id

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.pop(ws, None)

    async def broadcast_state(self, state_for: Any) -> None:
        """state_for(device_id) -> per-viewer state dict (vote flags differ)."""
        async with self._lock:
            targets = list(self.clients.items())
        for ws, device_id in targets:
            payload = json.dumps({"type": "state", "state": state_for(device_id)})
            with contextlib.suppress(Exception):
                await ws.send_text(payload)
