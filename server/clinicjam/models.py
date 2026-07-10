"""Shared data types for tracks and queue entries."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Track:
    uri: str
    name: str
    artists: str
    album: str
    art_url: str
    duration_ms: int
    explicit: bool

    @classmethod
    def from_spotify(cls, item: dict[str, Any]) -> "Track":
        images = item.get("album", {}).get("images", [])
        # Prefer the mid-size album image; fall back to whatever exists.
        art = images[1]["url"] if len(images) > 1 else (images[0]["url"] if images else "")
        return cls(
            uri=item["uri"],
            name=item["name"],
            artists=", ".join(a["name"] for a in item.get("artists", [])),
            album=item.get("album", {}).get("name", ""),
            art_url=art,
            duration_ms=item.get("duration_ms", 0),
            explicit=bool(item.get("explicit", False)),
        )

    def public(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "artists": self.artists,
            "album": self.album,
            "art_url": self.art_url,
            "duration_ms": self.duration_ms,
            "explicit": self.explicit,
        }


@dataclass
class QueueItem:
    track: Track
    device_id: str
    device_name: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    added_at: float = field(default_factory=time.time)
    votes: set[str] = field(default_factory=set)

    def public(self, viewer_device: str = "") -> dict[str, Any]:
        return {
            "id": self.id,
            "track": self.track.public(),
            "added_by": self.device_name or "someone",
            "votes": len(self.votes),
            "voted": viewer_device in self.votes,
            "mine": viewer_device == self.device_id,
        }
