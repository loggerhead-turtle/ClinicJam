"""Tiny bounded caches. Everything in here has a hard size cap so that a
guest hammering an endpoint can never make the server's memory grow."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    def __init__(self, max_items: int, ttl: float):
        self.max_items = max_items
        self.ttl = ttl
        self._data: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        at, value = hit
        if time.monotonic() - at > self.ttl:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def put(self, key: K, value: V) -> None:
        self._data[key] = (time.monotonic(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)


class ByteLRU:
    """LRU keyed by string with a total-bytes budget (for album art)."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self.size = 0
        self._data: OrderedDict[str, tuple[bytes, str]] = OrderedDict()

    def get(self, key: str) -> tuple[bytes, str] | None:
        hit = self._data.get(key)
        if hit is not None:
            self._data.move_to_end(key)
        return hit

    def put(self, key: str, body: bytes, content_type: str) -> None:
        if len(body) > self.max_bytes // 4:
            return  # one oversized entry must not evict everything else
        old = self._data.pop(key, None)
        if old:
            self.size -= len(old[0])
        self._data[key] = (body, content_type)
        self.size += len(body)
        while self.size > self.max_bytes and self._data:
            _, (evicted, _) = self._data.popitem(last=False)
            self.size -= len(evicted)
