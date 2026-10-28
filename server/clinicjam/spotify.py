"""Spotify Web API client for the single office host account.

Two ways to authorize, both completed with a copy-paste of the redirect
URL in the admin panel (Spotify only allows plain-HTTP redirects to
127.0.0.1, which a phone's browser can't deliver to the Pi):

* PKCE with Spotify's desktop client id (the default, zero setup). The
  same token logs the librespot player in, and the Web API accepts it.
* A developer app (client id + secret) for the Web API, if configured.
  The player is still linked with the PKCE flow in that case.

Spotify ids coming from guests are validated as 22-char base62 before
they are ever placed in a URL.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from .config import PLAYER_REDIRECT_URI, Config, StateStore
from .models import Track, spotify_id

log = logging.getLogger(__name__)

API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"
DEV_APP_SCOPES = (
    "user-read-playback-state user-modify-playback-state "
    "playlist-read-private playlist-read-collaborative"
)
# What Spotify's own desktop client requests; librespot asks for the same.
PLAYER_SCOPES = (
    "app-remote-control playlist-read playlist-read-collaborative "
    "playlist-read-private streaming user-library-read "
    "user-modify-playback-state user-read-currently-playing "
    "user-read-playback-position user-read-playback-state user-read-private"
)
MAX_RETRY_AFTER = 5


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
        self._pkce_verifier = ""   # pending PKCE login
        self._oauth_state = ""     # CSRF token for the pending login
        self._device_cache: tuple[float, str | None] = (0.0, None)

    @property
    def connected(self) -> bool:
        return bool(self.state.data.get("spotify_refresh_token"))

    @property
    def token_kind(self) -> str:
        kind = self.state.data.get("spotify_token_kind") or ""
        if not kind and self.connected:
            kind = "dev_app"  # tokens from before the PKCE flow existed
        return kind

    @property
    def player_linked(self) -> bool:
        """True once librespot has cached reusable credentials."""
        return (Path(self.config.librespot_cache_dir) / "credentials.json").is_file()

    # ---------------- auth ----------------

    def auth_url(self) -> str:
        """Authorization URL for the Web API link (dev app or PKCE)."""
        if self.config.uses_dev_app:
            self._oauth_state = secrets.token_urlsafe(16)
            params = {
                "client_id": self.config.spotify_client_id,
                "response_type": "code",
                "redirect_uri": self.config.spotify_redirect_uri,
                "scope": DEV_APP_SCOPES,
                "state": self._oauth_state,
            }
            return f"{ACCOUNTS}/authorize?{urllib.parse.urlencode(params)}"
        return self.player_auth_url()

    def player_auth_url(self) -> str:
        """PKCE authorization URL using the player's client id."""
        self._pkce_verifier = secrets.token_urlsafe(64)
        self._oauth_state = secrets.token_urlsafe(16)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(self._pkce_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        params = {
            "client_id": self.config.player_client_id,
            "response_type": "code",
            "redirect_uri": PLAYER_REDIRECT_URI,
            "scope": PLAYER_SCOPES,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": self._oauth_state,
        }
        return f"{ACCOUNTS}/authorize?{urllib.parse.urlencode(params)}"

    def _code_from(self, redirected_url: str) -> str:
        query = urllib.parse.urlparse(redirected_url.strip()).query
        params = urllib.parse.parse_qs(query)
        if "error" in params:
            raise SpotifyError(f"Spotify authorization failed: {params['error'][0]}")
        codes = params.get("code")
        if not codes:
            raise SpotifyError("No ?code= found in that URL — paste the full URL.")
        got_state = (params.get("state") or [""])[0]
        if not self._oauth_state or not secrets.compare_digest(got_state, self._oauth_state):
            raise SpotifyError(
                "That link doesn't match the login you started — tap the login button again."
            )
        self._oauth_state = ""
        return codes[0]

    async def complete_auth(self, redirected_url: str) -> None:
        """Finish the Web API link from the pasted redirect URL.

        With the default (no developer app) this also hands the token to
        the player, so one paste links everything.
        """
        code = self._code_from(redirected_url)
        if self.config.uses_dev_app:
            data = await self._token_request(
                grant_type="authorization_code",
                code=code,
                redirect_uri=self.config.spotify_redirect_uri,
            )
            kind = "dev_app"
        else:
            data = await self._pkce_exchange(code)
            kind = "pkce"
            self._hand_token_to_player(data["access_token"])
        if not data.get("refresh_token"):
            raise SpotifyError("Spotify did not return a refresh token")
        self.state.data["spotify_refresh_token"] = data["refresh_token"]
        self.state.data["spotify_token_kind"] = kind
        self.state.save()
        self._store_access(data)

    async def complete_player_auth(self, redirected_url: str) -> None:
        """Finish a player-only link (used when a developer app handles the API)."""
        code = self._code_from(redirected_url)
        data = await self._pkce_exchange(code)
        self._hand_token_to_player(data["access_token"])

    async def _pkce_exchange(self, code: str) -> dict[str, Any]:
        if not self._pkce_verifier:
            raise SpotifyError("Start the login first (the login link expired).")
        verifier, self._pkce_verifier = self._pkce_verifier, ""
        resp = await self._http.post(
            f"{ACCOUNTS}/api/token",
            data={
                "client_id": self.config.player_client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": PLAYER_REDIRECT_URI,
                "code_verifier": verifier,
            },
        )
        if resp.status_code != 200:
            raise SpotifyError(f"Token request failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    def _hand_token_to_player(self, access_token: str) -> None:
        """Drop a one-shot token where the librespot launcher picks it up."""
        path = Path(self.config.librespot_token_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(access_token)
        except OSError as e:
            log.warning("could not write player token file %s: %s", path, e)

    async def _token_request(self, **form: str) -> dict[str, Any]:
        if self.token_kind == "pkce" and form.get("grant_type") == "refresh_token":
            form["client_id"] = self.config.player_client_id
            headers = {}
        else:
            basic = base64.b64encode(
                f"{self.config.spotify_client_id}:{self.config.spotify_client_secret}".encode()
            ).decode()
            headers = {"Authorization": f"Basic {basic}"}
        resp = await self._http.post(f"{ACCOUNTS}/api/token", data=form, headers=headers)
        if resp.status_code != 200:
            raise SpotifyError(f"Token request failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    def _store_access(self, data: dict[str, Any]) -> None:
        self._access_token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600)) - 60
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

    def disconnect(self) -> None:
        self._access_token = ""
        self.state.data["spotify_refresh_token"] = ""
        self.state.data["spotify_token_kind"] = ""
        self.state.save()

    # ---------------- API helpers ----------------

    async def _request(
        self, method: str, path: str, *, retries: int = 2, **kwargs: Any
    ) -> httpx.Response:
        token = await self._token()
        try:
            resp = await self._http.request(
                method, f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
        except httpx.HTTPError as e:
            raise SpotifyError(f"Spotify unreachable: {e.__class__.__name__}") from e
        if resp.status_code == 429 and retries > 0:
            try:
                wait = int(resp.headers.get("Retry-After", "1"))
            except ValueError:
                wait = 1
            if wait > MAX_RETRY_AFTER:
                raise SpotifyError(f"Spotify is rate limiting us (retry in {wait}s)")
            await asyncio.sleep(wait + 0.5)
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
            params={"q": query[:100], "type": "track", "limit": limit, "market": "from_token"},
        )
        items = resp.json().get("tracks", {}).get("items", []) or []
        return [Track.from_spotify(t) for t in items if t]

    async def track(self, uri_or_id: str) -> Track:
        track_id = spotify_id(uri_or_id, "track")
        if not track_id:
            raise SpotifyError("Not a valid Spotify track")
        resp = await self._request(
            "GET", f"/tracks/{track_id}", params={"market": "from_token"}
        )
        return Track.from_spotify(resp.json())

    async def playlist_tracks(self, playlist: str, limit: int = 100) -> list[Track]:
        """Resolve a playlist URI/URL/ID to its (playable) tracks."""
        pl_id = spotify_id(playlist, "playlist")
        if not pl_id:
            raise SpotifyError("Not a valid Spotify playlist link")
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
            for entry in body.get("items", []) or []:
                t = entry.get("track")
                if t and str(t.get("uri", "")).startswith("spotify:track:"):
                    tracks.append(Track.from_spotify(t))
            if not body.get("next"):
                break
            offset += 50
        return tracks

    # ---------------- playback ----------------

    async def find_device(self, max_age: float = 0.0) -> str | None:
        """Device id of our librespot Connect endpoint, if visible.

        `max_age` lets callers that only need a status indicator reuse a
        recent answer instead of hitting the API on every poll.
        """
        at, cached = self._device_cache
        if max_age and time.monotonic() - at < max_age:
            return cached
        resp = await self._request("GET", "/me/player/devices")
        found = None
        for dev in resp.json().get("devices", []) or []:
            if dev.get("name") == self.config.spotify_device_name:
                found = dev.get("id")
                break
        self._device_cache = (time.monotonic(), found)
        return found

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

    async def fetch_art(self, url: str) -> tuple[bytes, str] | None:
        """Fetch album art from Spotify's CDN (host pre-validated by caller)."""
        try:
            resp = await self._http.get(url, follow_redirects=False, timeout=8)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200 or len(resp.content) > 512 * 1024:
            return None
        ctype = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        if not ctype.startswith("image/"):
            return None
        return resp.content, ctype
