"""Spotify Web API client for the single office host account.

Uses the authorization-code flow. Because Spotify only allows plain-HTTP
redirect URIs on 127.0.0.1, the admin completes auth by pasting the
redirected URL back into the admin panel (see docs/SPOTIFY_SETUP.md).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import urllib.parse
from typing import Any

import httpx

from .config import Config, StateStore
from .models import Track

log = logging.getLogger(__name__)

API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"
SCOPES = (
    "user-read-playback-state user-modify-playback-state "
    "playlist-read-private playlist-read-collaborative"
)


class SpotifyError(Exception):
    pass


class SpotifyNotConnected(SpotifyError):
    pass


class SpotifyClient:
    def __init__(self, config: Config, state: StateStore):
        self.config = config
        self.state = state
        self._access_token = ""
        self._token_expiry = 0.0
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=15)

    @property
    def connected(self) -> bool:
        return bool(self.state.data.get("spotify_refresh_token"))

    # ---------------- auth ----------------

    def auth_url(self) -> str:
        if not self.config.spotify_client_id:
            raise SpotifyError(
                "Spotify client_id is not set in /etc/clinicjam/config.toml"
            )
        params = {
            "client_id": self.config.spotify_client_id,
            "response_type": "code",
            "redirect_uri": self.config.spotify_redirect_uri,
            "scope": SCOPES,
        }
        return f"{ACCOUNTS}/authorize?{urllib.parse.urlencode(params)}"

    async def complete_auth(self, redirected_url: str) -> None:
        """Exchange the code from the pasted redirect URL for tokens."""
        query = urllib.parse.urlparse(redirected_url.strip()).query
        params = urllib.parse.parse_qs(query)
        if "error" in params:
            raise SpotifyError(f"Spotify authorization failed: {params['error'][0]}")
        codes = params.get("code")
        if not codes:
            raise SpotifyError("No ?code= found in that URL — paste the full URL.")
        data = await self._token_request(
            grant_type="authorization_code",
            code=codes[0],
            redirect_uri=self.config.spotify_redirect_uri,
        )
        self.state.data["spotify_refresh_token"] = data["refresh_token"]
        self.state.save()
        self._store_access(data)

    async def _token_request(self, **form: str) -> dict[str, Any]:
        basic = base64.b64encode(
            f"{self.config.spotify_client_id}:{self.config.spotify_client_secret}".encode()
        ).decode()
        resp = await self._http.post(
            f"{ACCOUNTS}/api/token",
            data=form,
            headers={"Authorization": f"Basic {basic}"},
        )
        if resp.status_code != 200:
            raise SpotifyError(f"Token request failed ({resp.status_code}): {resp.text}")
        return resp.json()

    def _store_access(self, data: dict[str, Any]) -> None:
        self._access_token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600) - 60
        if data.get("refresh_token"):
            self.state.data["spotify_refresh_token"] = data["refresh_token"]
            self.state.save()

    async def _token(self) -> str:
        async with self._lock:
            if self._access_token and time.time() < self._token_expiry:
                return self._access_token
            refresh = self.state.data.get("spotify_refresh_token")
            if not refresh:
                raise SpotifyNotConnected("Spotify account not connected yet")
            data = await self._token_request(
                grant_type="refresh_token", refresh_token=refresh
            )
            self._store_access(data)
            return self._access_token

    # ---------------- API helpers ----------------

    async def _request(
        self, method: str, path: str, *, retries: int = 2, **kwargs: Any
    ) -> httpx.Response:
        token = await self._token()
        resp = await self._http.request(
            method, f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs
        )
        if resp.status_code == 429 and retries > 0:
            await asyncio.sleep(int(resp.headers.get("Retry-After", "1")) + 1)
            return await self._request(method, path, retries=retries - 1, **kwargs)
        if resp.status_code == 401 and retries > 0:
            self._access_token = ""  # force refresh
            return await self._request(method, path, retries=retries - 1, **kwargs)
        if resp.status_code >= 400:
            raise SpotifyError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp

    # ---------------- catalog ----------------

    async def search(self, query: str, limit: int = 12) -> list[Track]:
        resp = await self._request(
            "GET",
            "/search",
            params={"q": query, "type": "track", "limit": limit, "market": "from_token"},
        )
        items = resp.json().get("tracks", {}).get("items", [])
        return [Track.from_spotify(t) for t in items]

    async def track(self, uri_or_id: str) -> Track:
        track_id = uri_or_id.rsplit(":", 1)[-1].split("?")[0].rsplit("/", 1)[-1]
        resp = await self._request(
            "GET", f"/tracks/{track_id}", params={"market": "from_token"}
        )
        return Track.from_spotify(resp.json())

    async def playlist_tracks(self, playlist: str, limit: int = 100) -> list[Track]:
        """Resolve a playlist URI/URL/ID to its (playable) tracks."""
        pl_id = playlist.rstrip("/").rsplit(":", 1)[-1].split("?")[0].rsplit("/", 1)[-1]
        tracks: list[Track] = []
        offset = 0
        while len(tracks) < limit:
            resp = await self._request(
                "GET",
                f"/playlists/{pl_id}/tracks",
                params={
                    "limit": 50,
                    "offset": offset,
                    "market": "from_token",
                    "fields": "items(track(uri,name,artists,album,duration_ms,explicit)),next",
                },
            )
            body = resp.json()
            for entry in body.get("items", []):
                t = entry.get("track")
                if t and t.get("uri", "").startswith("spotify:track:"):
                    tracks.append(Track.from_spotify(t))
            if not body.get("next"):
                break
            offset += 50
        return tracks

    # ---------------- playback ----------------

    async def find_device(self) -> str | None:
        """Return the device id of our librespot Connect endpoint, if visible."""
        resp = await self._request("GET", "/me/player/devices")
        for dev in resp.json().get("devices", []):
            if dev.get("name") == self.config.spotify_device_name:
                return dev.get("id")
        return None

    async def play(self, uri: str, device_id: str) -> None:
        await self._request(
            "PUT",
            "/me/player/play",
            params={"device_id": device_id},
            json={"uris": [uri]},
        )

    async def pause(self) -> None:
        try:
            await self._request("PUT", "/me/player/pause")
        except SpotifyError as e:
            # 403 "already paused" is harmless.
            log.debug("pause: %s", e)

    async def resume(self) -> None:
        try:
            await self._request("PUT", "/me/player/play")
        except SpotifyError as e:
            log.debug("resume: %s", e)

    async def playback_state(self) -> dict[str, Any] | None:
        resp = await self._request("GET", "/me/player")
        if resp.status_code == 204 or not resp.text:
            return None
        return resp.json()
