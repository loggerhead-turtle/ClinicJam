"""Shared data types for tracks and queue entries."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

# Album art lives on Spotify's CDN, which guests cannot reach from the
# walled garden — so the Pi proxies it (see /api/art in main.py).
ART_HOST_RE = re.compile(r"^https://i\.scdn\.co/image/[0-9a-f]{40}$")
SPOTIFY_ID_RE = re.compile(r"^[0-9A-Za-z]{22}$")


def spotify_id(value: str, kind: str) -> str | None:
    """Extract and validate a 22-char base62 Spotify id from a URI/URL/id."""
    value = value.strip()
    candidate = value
    if value.startswith("spotify:"):
        parts = value.split(":")
        if len(parts) == 3 and parts[1] == kind:
            candidate = parts[2]
        else:
            return None
    elif "open.spotify.com/" in value:
        path = value.split("open.spotify.com/", 1)[1].split("?", 1)[0].rstrip("/")
        segs = path.split("/")
        # /track/<id>, /playlist/<id>, /intl-xx/track/<id>, /user/x/playlist/<id>
        try:
            idx = len(segs) - 1 - segs[::-1].index(kind)
        except ValueError:
            return None
        if idx + 1 >= len(segs):
            return None
        candidate = segs[idx + 1]
    return candidate if SPOTIFY_ID_RE.match(candidate) else None


def proxied_art(url: str) -> str:
    if url and ART_HOST_RE.match(url):
        return "/api/art?u=" + quote(url, safe="")
    return ""


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
        images = item.get("album", {}).get("images", []) or []
        # Prefer the mid-size album image; fall back to whatever exists.
        art = images[1]["url"] if len(images) > 1 else (images[0]["url"] if images else "")
        return cls(
            uri=str(item["uri"]),
            name=str(item.get("name", ""))[:200],
            artists=", ".join(str(a.get("name", "")) for a in item.get("artists", []))[:200],
            album=str(item.get("album", {}).get("name", ""))[:200],
            art_url=str(art or ""),
            duration_ms=int(item.get("duration_ms", 0) or 0),
            explicit=bool(item.get("explicit", False)),
        )

    def public(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "artists": self.artists,
            "album": self.album,
            "art_url": proxied_art(self.art_url),
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
