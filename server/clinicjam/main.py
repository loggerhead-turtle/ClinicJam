"""ClinicJam web server: guest app, admin panel, captive portal, audio stream."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import unquote

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.websockets import WebSocket, WebSocketDisconnect

from .auth import ADMIN_TTL, GUEST_TTL, KIOSK_ADMIN_TTL, LoginLimiter, RateLimiter, SessionSigner
from .cache import ByteLRU, TTLCache
from .cast import CastController
from .config import Config, StateStore
from .events import Hub, HubFull
from .jam import Jam, JamError
from .models import ART_HOST_RE, Track
from .netinfo import ArpCache, HelperQueue, normalize_mac
from .player import Player
from .spotify import SpotifyClient, SpotifyError
from .stream import AudioStreamer, StreamFull

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("clinicjam")

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
MAX_BODY_BYTES = 16 * 1024

config = Config.load()
for warning in config.warnings():
    log.warning("CONFIG: %s", warning)
state = StateStore(config.state_file)
jam = Jam(state.settings)
jam.banned = set(state.data.get("banned_devices", []))
banned_macs: set[str] = set(state.data.get("banned_macs", []))
spotify = SpotifyClient(config, state)
streamer = AudioStreamer(config.fifo_path, config.stream_bitrate, config.max_stream_clients)
stream_url = f"http://{config.ap_ip}:{config.server_port}/stream.mp3"
cast = CastController(stream_url)
cast.set_target(state.settings.get("cast_target", ""))
player = Player(jam, spotify, cast, state)
hub = Hub()
signer = SessionSigner(
    state.session_secret, {"admin": config.admin_pin, "guest": config.guest_pin}
)
login_limiter = LoginLimiter()
search_limiter = RateLimiter(limit=40, window=60)
write_limiter = RateLimiter(limit=60, window=60)
art_limiter = RateLimiter(limit=60, window=60)
search_cache: TTLCache[str, list[Track]] = TTLCache(max_items=300, ttl=600)
track_cache: TTLCache[str, Track] = TTLCache(max_items=1000, ttl=3600)
art_cache = ByteLRU(max_bytes=12 * 1024 * 1024)
arp = ArpCache()
helper = HelperQueue(config.run_dir)
hook_token = secrets.token_urlsafe(32)
_background: set[asyncio.Task] = set()
_broadcast_pending = False

# Hostnames/paths phones probe to decide whether a captive portal exists.
PORTAL_PROBE_PATHS = {
    "/generate_204", "/gen_204",                 # Android
    "/hotspot-detect.html", "/library/test/success.html",  # Apple
    "/connecttest.txt", "/ncsi.txt", "/redirect",          # Windows
    "/success.txt", "/canonical.html",                     # Firefox/NM
}
KNOWN_HOSTS = {config.ap_ip, "localhost", "127.0.0.1", "clinicjam", "clinicjam.lan", ""}
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (
        b"content-security-policy",
        b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        b"script-src 'self'; connect-src 'self' ws: wss:; frame-ancestors 'none'; "
        b"form-action 'self'; base-uri 'none'",
    ),
]


# ---------------------------------------------------------------- helpers

def _pin_matches(given: str, expected: str) -> bool:
    if not expected:
        return False  # an unset PIN never matches anything
    return secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def _spawn(coro, name: str) -> None:
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _background.add(task)
    task.add_done_callback(_background.discard)


def _schedule_broadcast() -> None:
    """Coalesce bursts: many votes in the same instant -> one broadcast."""
    global _broadcast_pending
    if _broadcast_pending:
        return
    _broadcast_pending = True

    async def _send() -> None:
        global _broadcast_pending
        try:
            await asyncio.sleep(0.05)
            _broadcast_pending = False
            await hub.broadcast_state(lambda dev: jam.public_state(dev))
        except Exception:
            _broadcast_pending = False
            log.exception("broadcast failed")

    with contextlib.suppress(RuntimeError):
        _spawn(_send(), "broadcast")


player.on_change = _schedule_broadcast


def _write_hook_token() -> None:
    """Shared secret for librespot's event hook (both run as `clinicjam`)."""
    path = Path(config.run_dir) / "hook.token"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as f:
            f.write(hook_token)
    except OSError as e:
        log.warning("cannot write %s (%s); event hook disabled", path, e)


def _on_cast_device_seen(uuid: str, host: str, name: str) -> None:
    """Discovery-thread callback: allowlist speakers for internet automatically."""
    if not config.auto_allow_cast_devices or not host:
        return
    loop = _main_loop
    if loop is None:
        return

    def _do() -> None:
        mac = arp.mac_for(host)
        if not mac:
            # Not in the neighbour table yet — the periodic sweep retries.
            return
        if mac in helper.state().get("allowed", {}):
            return
        log.info("auto-allowlisting cast device %s (%s, %s)", name, host, mac)
        helper.allow_mac(mac, name or "speaker")

    loop.call_soon_threadsafe(_do)


_main_loop: asyncio.AbstractEventLoop | None = None


async def _lifespan(app: FastAPI):
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    _write_hook_token()
    cast.on_device_seen = _on_cast_device_seen
    await streamer.start()
    await cast.start_discovery()
    await player.start()

    async def ticker() -> None:
        # Progress bars advance client-side; a slow heartbeat keeps
        # listener counts and stragglers fresh. Also the housekeeping beat.
        n = 0
        while True:
            await asyncio.sleep(15)
            n += 1
            _schedule_broadcast()
            if n % 4 == 0:
                jam.prune_devices()
                _sweep_cast_allowlist()

    tick = asyncio.create_task(ticker(), name="ticker")
    try:
        yield
    finally:
        tick.cancel()
        await player.stop()
        await streamer.stop()
        await cast.stop()


def _sweep_cast_allowlist() -> None:
    if not config.auto_allow_cast_devices:
        return
    allowed = helper.state().get("allowed", {})
    for dev in cast.devices():
        mac = arp.mac_for(dev["host"]) if dev["host"] else None
        if mac and mac not in allowed:
            log.info("auto-allowlisting cast device %s (%s)", dev["name"], mac)
            helper.allow_mac(mac, dev["name"] or "speaker")


app = FastAPI(title="ClinicJam", lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(SpotifyError)
async def _spotify_error(_request: Request, exc: SpotifyError):
    return JSONResponse({"detail": f"Spotify: {exc}"[:300]}, status_code=503)


# ---------------------------------------------------------------- ASGI middleware
#
# Pure ASGI (not BaseHTTPMiddleware) so the live MP3 stream and websockets
# pass through without an extra buffering layer.

class BodyLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        return await JSONResponse({"detail": "Request too large"}, 413)(scope, receive, send)
                except ValueError:
                    return await JSONResponse({"detail": "Bad request"}, 400)(scope, receive, send)
        seen = 0

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise HTTPException(413, "Request too large")
            return message

        return await self.app(scope, limited_receive, send)


class PortalAndHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path in PORTAL_PROBE_PATHS:
            # A redirect (not a 204/success body) tells the OS "captive portal
            # here" and pops the sign-in sheet, which we point at the jam.
            return await RedirectResponse(f"http://{config.ap_ip}/", status_code=302)(scope, receive, send)
        host = ""
        for name, value in scope.get("headers", []):
            if name == b"host":
                host = value.decode("latin-1").split(":")[0].lower()
                break
        # Only whole-page navigations get herded to the portal; API, stream,
        # and websocket traffic must never be redirected.
        is_page = not (
            path.startswith("/api") or path.startswith("/static")
            or path in ("/ws", "/stream.mp3", "/captive-portal-api")
        )
        if is_page and host not in KNOWN_HOSTS:
            return await RedirectResponse(f"http://{config.ap_ip}/", status_code=302)(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {h[0] for h in headers}
                for name, value in SECURITY_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                message["headers"] = headers
            await send(message)

        return await self.app(scope, receive, send_with_headers)


app.add_middleware(PortalAndHeadersMiddleware)
app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)


@app.get("/captive-portal-api")
async def rfc8910():
    """RFC 8910 Captive Portal API (advertised via DHCP option 114)."""
    return JSONResponse(
        {
            "captive": False,
            "user-portal-url": f"http://{config.ap_ip}/",
            "venue-info-url": f"http://{config.ap_ip}/",
        },
        media_type="application/captive+json",
    )


# ---------------------------------------------------------------- sessions
#
# Two tiers:
#   guest  — the shared jam PIN; may search, queue, and vote. Nothing else.
#   admin  — the host PIN; everything, including settings. An admin session
#            also counts as a guest session.
# Tokens are HMAC-signed (auth.py): nothing is stored server-side.

def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _is_admin(request: Request) -> bool:
    return signer.verify(request.cookies.get("clinicjam_admin", ""), "admin") is not None


def _is_guest(request: Request) -> bool:
    if not config.guest_pin:  # gate disabled by config
        return True
    return (
        signer.verify(request.cookies.get("clinicjam_guest", ""), "guest") is not None
        or _is_admin(request)
    )


def require_guest(request: Request) -> None:
    if not _is_guest(request):
        raise HTTPException(401, "Enter the jam PIN to join")
    ip = _client_ip(request)
    mac = arp.mac_for(ip) if ip else None
    if mac and mac in banned_macs:
        raise HTTPException(403, "This device has been blocked by the host.")


def require_admin(request: Request) -> None:
    if not _is_admin(request):
        raise HTTPException(401, "Admin login required")


async def _check_login(request: Request, pin: str, valid: bool) -> None:
    ip = _client_ip(request) or "?"
    wait = login_limiter.locked_for(ip)
    if wait > 0:
        raise HTTPException(429, f"Too many attempts — try again in {int(wait) + 1}s")
    delay = login_limiter.global_delay()
    if delay:
        await asyncio.sleep(delay)
    if not valid:
        lock = login_limiter.record_failure(ip)
        log.warning("failed PIN attempt from %s", ip)
        if lock:
            raise HTTPException(429, f"Too many attempts — try again in {int(lock)}s")
        raise HTTPException(401, "Wrong PIN")
    login_limiter.record_success(ip)


class PinBody(BaseModel):
    pin: str = Field(max_length=64)


@app.post("/api/login")
async def guest_login(body: PinBody, request: Request, response: Response):
    # The admin PIN is accepted here too, so the host never has to juggle.
    valid = _pin_matches(body.pin, config.guest_pin) or _pin_matches(body.pin, config.admin_pin)
    if config.guest_pin:
        await _check_login(request, body.pin, valid)
    device_id, _ = _device(request, counted=True)
    token = signer.issue("guest", GUEST_TTL, device_id)
    response.set_cookie(
        "clinicjam_guest", token, httponly=True, samesite="lax", max_age=GUEST_TTL
    )
    return {"ok": True}


@app.post("/api/logout")
async def guest_logout(response: Response):
    response.delete_cookie("clinicjam_guest")
    response.delete_cookie("clinicjam_admin")
    return {"ok": True}


@app.get("/api/session")
async def session_info(request: Request):
    return {
        "guest_pin_required": bool(config.guest_pin),
        "guest": _is_guest(request),
        "admin": _is_admin(request),
    }


# ---------------------------------------------------------------- guest API

_DEVICE_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _device(request: Request, counted: bool | None = None) -> tuple[str, str]:
    raw = request.headers.get("x-device-id", "")[:40]
    device_id = "".join(ch for ch in raw if ch in _DEVICE_SAFE)
    name = request.headers.get("x-device-name", "")[:24]
    if device_id:
        if counted is None:
            counted = _is_guest(request)  # only joined guests count as listeners
        jam.touch_device(device_id, name, _client_ip(request), counted=counted)
    return device_id, name


@app.get("/api/state")
async def api_state(request: Request):
    device_id, _ = _device(request)
    return jam.public_state(device_id)


@app.get("/api/search", dependencies=[Depends(require_guest)])
async def api_search(q: str, request: Request):
    _device(request)
    q = q.strip()[:100]
    if len(q) < 2:
        return {"tracks": []}
    key = q.lower()
    tracks = search_cache.get(key)
    if tracks is None:
        if not search_limiter.allow(_client_ip(request)):
            raise HTTPException(429, "Slow down a little — searching too fast")
        try:
            tracks = await spotify.search(q)
        except SpotifyError as e:
            raise HTTPException(503, f"Search unavailable: {e}")
        search_cache.put(key, tracks)
        for t in tracks:
            track_cache.put(t.uri, t)
    if state.settings.get("explicit_filter"):
        tracks = [t for t in tracks if not t.explicit]
    return {"tracks": [t.public() for t in tracks]}


@app.get("/api/art")
async def api_art(u: str, request: Request):
    """Album art proxy: guests can't reach Spotify's CDN through the wall."""
    url = unquote(u)
    if not ART_HOST_RE.match(url):
        raise HTTPException(404)
    hit = art_cache.get(url)
    if hit is None:
        if not art_limiter.allow(_client_ip(request)):
            raise HTTPException(429, "Too many image requests")
        fetched = await spotify.fetch_art(url)
        if fetched is None:
            raise HTTPException(404)
        art_cache.put(url, *fetched)
        hit = fetched
    body, ctype = hit
    return Response(body, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})


class AddBody(BaseModel):
    uri: str = Field(max_length=64)


@app.post("/api/queue", dependencies=[Depends(require_guest)])
async def api_add(body: AddBody, request: Request):
    device_id, name = _device(request)
    if not device_id:
        raise HTTPException(400, "Missing device id")
    if not body.uri.startswith("spotify:track:"):
        raise HTTPException(400, "Not a Spotify track")
    if not write_limiter.allow(_client_ip(request)):
        raise HTTPException(429, "Slow down a little")
    try:
        track = track_cache.get(body.uri)
        if track is None:
            track = await spotify.track(body.uri)
            track_cache.put(track.uri, track)
        item = jam.add(track, device_id, name)
    except JamError as e:
        raise HTTPException(409, str(e))
    except SpotifyError as e:
        raise HTTPException(503, str(e))
    player.wake()
    _schedule_broadcast()
    return item.public(device_id)


class VoteBody(BaseModel):
    up: bool = True


@app.post("/api/queue/{item_id}/vote", dependencies=[Depends(require_guest)])
async def api_vote(item_id: str, body: VoteBody, request: Request):
    device_id, _ = _device(request)
    if not device_id:
        raise HTTPException(400, "Missing device id")
    if not write_limiter.allow(_client_ip(request)):
        raise HTTPException(429, "Slow down a little")
    try:
        jam.vote(item_id[:32], device_id, body.up)
    except JamError as e:
        raise HTTPException(409, str(e))
    _schedule_broadcast()
    return jam.public_state(device_id)


@app.post("/api/skip-vote", dependencies=[Depends(require_guest)])
async def api_skip_vote(request: Request):
    device_id, _ = _device(request)
    if not device_id:
        raise HTTPException(400, "Missing device id")
    if not write_limiter.allow(_client_ip(request)):
        raise HTTPException(429, "Slow down a little")
    try:
        should_skip = jam.vote_skip(device_id)
    except JamError as e:
        raise HTTPException(409, str(e))
    if should_skip:
        await player.skip()
    _schedule_broadcast()
    return jam.public_state(device_id)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    raw = ws.query_params.get("device", "")[:40]
    device_id = "".join(ch for ch in raw if ch in _DEVICE_SAFE)
    ip = ws.client.host if ws.client else ""
    joined = (not config.guest_pin
              or signer.verify(ws.cookies.get("clinicjam_guest", ""), "guest") is not None
              or signer.verify(ws.cookies.get("clinicjam_admin", ""), "admin") is not None)
    jam.touch_device(device_id, "", ip, counted=joined)
    try:
        await hub.connect(ws, device_id)
    except HubFull:
        return
    try:
        await ws.send_json({"type": "state", "state": jam.public_state(device_id)})
        while True:
            msg = await ws.receive_text()  # pings keep the device 'active'
            if len(msg) > 64:
                break
            jam.touch_device(device_id, counted=joined)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await hub.disconnect(ws)


# ---------------------------------------------------------------- stream

def _may_stream(request: Request) -> bool:
    ip = _client_ip(request)
    if ip in ("127.0.0.1", "::1") or _is_admin(request):
        return True
    if ip and ip in cast.device_hosts():
        return True
    mac = arp.mac_for(ip) if ip else None
    return bool(mac and mac in helper.state().get("allowed", {}))


@app.get("/stream.mp3")
async def stream_mp3(request: Request):
    # The stream is for the speakers (and the host). A guest phone pulling
    # it would cost encoder CPU and Wi-Fi airtime for nothing.
    if not _may_stream(request):
        raise HTTPException(403, "The live stream is only served to the speakers")
    gen = streamer.broadcaster.subscribe()
    try:
        first = await gen.__anext__()
    except StreamFull:
        raise HTTPException(503, "Too many stream listeners")

    async def body():
        yield first
        async for chunk in gen:
            yield chunk

    return StreamingResponse(
        body(),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "icy-name": "ClinicJam"},
    )


# ---------------------------------------------------------------- internal

class EventBody(BaseModel):
    event: str = Field(max_length=64)


@app.post("/api/internal/librespot-event")
async def librespot_event(body: EventBody, request: Request):
    if _client_ip(request) not in ("127.0.0.1", "::1"):
        raise HTTPException(403)
    auth = request.headers.get("authorization", "")
    if not (auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], hook_token)):
        raise HTTPException(403)
    player.notify_librespot_event(body.event)
    return {"ok": True}


# ---------------------------------------------------------------- admin

class AdminPinBody(PinBody):
    # The shared touchscreen asks for a short session so a host who walks
    # away doesn't leave the panel unlocked for hours.
    short: bool = False


@app.post("/api/admin/login")
async def admin_login(body: AdminPinBody, request: Request, response: Response):
    if not config.admin_pin:
        raise HTTPException(403, "No admin PIN is configured — set one in config.toml")
    valid = _pin_matches(body.pin, config.admin_pin)
    await _check_login(request, body.pin, valid)
    ttl = KIOSK_ADMIN_TTL if body.short else ADMIN_TTL
    token = signer.issue("admin", ttl)
    response.set_cookie(
        "clinicjam_admin", token, httponly=True, samesite="lax", max_age=ttl
    )
    log.info("admin login from %s", _client_ip(request))
    return {"ok": True}


@app.post("/api/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie("clinicjam_admin")
    return {"ok": True}


@app.get("/api/admin/status", dependencies=[Depends(require_admin)])
async def admin_status():
    librespot_seen = False
    if spotify.connected:
        with contextlib.suppress(SpotifyError):
            librespot_seen = bool(await spotify.find_device(max_age=30))
    fw = helper.state()
    return {
        "spotify_connected": spotify.connected,
        "spotify_token_kind": spotify.token_kind,
        "uses_dev_app": config.uses_dev_app,
        "player_linked": spotify.player_linked,
        "librespot_visible": librespot_seen,
        "cast": await cast.status(),
        "cast_devices": cast.devices(),
        "settings": state.settings,
        "jam_active": jam.active,
        "paused": jam.paused,
        "listeners": jam.active_device_count(),
        "banned": sorted(jam.banned),
        "devices": [
            {"id": did, "name": info["name"] or "(unnamed)", "ip": info.get("ip", "")}
            for did, info in jam.devices.items()
        ],
        "last_error": player.last_error,
        "encoder_running": streamer.encoder_running,
        "stream_listeners": len(streamer.broadcaster.subscribers),
        "network": {
            "helper_available": helper.available,
            "allowed": fw.get("allowed", {}),
            "setup_mode_until": fw.get("setup_mode_until", 0),
            "setup_mode_remaining": max(0, int(fw.get("setup_mode_until", 0) - time.time())),
        },
        "config_warnings": config.warnings(),
    }


@app.get("/api/admin/spotify/auth-url", dependencies=[Depends(require_admin)])
async def admin_auth_url():
    return {"url": spotify.auth_url(), "player_included": not config.uses_dev_app}


class CallbackBody(BaseModel):
    url: str = Field(max_length=4096)


@app.post("/api/admin/spotify/callback", dependencies=[Depends(require_admin)])
async def admin_auth_callback(body: CallbackBody):
    await spotify.complete_auth(body.url)
    if not config.uses_dev_app:
        helper.restart_player()
    spotify._device_cache = (0.0, None)
    return {"ok": True}


@app.get("/api/admin/spotify/player/auth-url", dependencies=[Depends(require_admin)])
async def admin_player_auth_url():
    return {"url": spotify.player_auth_url()}


@app.post("/api/admin/spotify/player/callback", dependencies=[Depends(require_admin)])
async def admin_player_auth_callback(body: CallbackBody):
    await spotify.complete_player_auth(body.url)
    helper.restart_player()
    return {"ok": True}


@app.post("/api/admin/spotify/disconnect", dependencies=[Depends(require_admin)])
async def admin_spotify_disconnect():
    await player.stop_jam()
    spotify.disconnect()
    return {"ok": True}


@app.post("/api/admin/player/restart", dependencies=[Depends(require_admin)])
async def admin_player_restart():
    return {"ok": helper.restart_player()}


class TargetBody(BaseModel):
    uuid: str = Field(max_length=64)


@app.post("/api/admin/cast/target", dependencies=[Depends(require_admin)])
async def admin_cast_target(body: TargetBody):
    cast.set_target(body.uuid)
    state.update_settings({"cast_target": body.uuid})
    if jam.active:
        await cast.ensure_casting()
    return {"ok": True}


class VolumeBody(BaseModel):
    level: float = Field(ge=0.0, le=1.0)
    uuid: str = Field(default="", max_length=64)


@app.post("/api/admin/cast/volume", dependencies=[Depends(require_admin)])
async def admin_cast_volume(body: VolumeBody):
    ok = await cast.set_volume(body.level, body.uuid)
    return {"ok": ok}


@app.post("/api/admin/jam/start", dependencies=[Depends(require_admin)])
async def admin_start():
    await player.start_jam()
    return {"ok": True}


@app.post("/api/admin/jam/stop", dependencies=[Depends(require_admin)])
async def admin_stop():
    await player.stop_jam()
    return {"ok": True}


@app.post("/api/admin/playback/skip", dependencies=[Depends(require_admin)])
async def admin_skip():
    await player.skip()
    return {"ok": True}


@app.post("/api/admin/playback/pause", dependencies=[Depends(require_admin)])
async def admin_pause():
    await player.pause()
    return {"ok": True}


@app.post("/api/admin/playback/resume", dependencies=[Depends(require_admin)])
async def admin_resume():
    await player.resume()
    return {"ok": True}


@app.delete("/api/admin/queue/{item_id}", dependencies=[Depends(require_admin)])
async def admin_remove(item_id: str):
    removed = jam.remove(item_id[:32])
    _schedule_broadcast()
    return {"removed": removed}


class BanBody(BaseModel):
    device_id: str = Field(max_length=40)


def _save_bans() -> None:
    state.data["banned_devices"] = sorted(jam.banned)
    state.data["banned_macs"] = sorted(banned_macs)
    state.save()


@app.post("/api/admin/ban", dependencies=[Depends(require_admin)])
async def admin_ban(body: BanBody):
    jam.banned.add(body.device_id)
    jam.remove_from_device(body.device_id)
    # Also block the physical device, so a cleared browser doesn't help.
    ip = (jam.devices.get(body.device_id) or {}).get("ip", "")
    mac = arp.mac_for(ip) if ip else None
    if mac:
        banned_macs.add(mac)
    _save_bans()
    _schedule_broadcast()
    return {"ok": True, "mac": mac or ""}


@app.delete("/api/admin/ban/{device_id}", dependencies=[Depends(require_admin)])
async def admin_unban(device_id: str):
    jam.banned.discard(device_id)
    ip = (jam.devices.get(device_id) or {}).get("ip", "")
    mac = arp.mac_for(ip) if ip else None
    if mac:
        banned_macs.discard(mac)
    _save_bans()
    return {"ok": True}


@app.delete("/api/admin/ban-mac/{mac}", dependencies=[Depends(require_admin)])
async def admin_unban_mac(mac: str):
    norm = normalize_mac(mac)
    if norm:
        banned_macs.discard(norm)
        _save_bans()
    return {"ok": True, "banned_macs": sorted(banned_macs)}


class SettingsBody(BaseModel):
    queue_mode: str | None = Field(default=None, max_length=8)
    explicit_filter: bool | None = None
    skip_ratio: float | None = None
    per_user_limit: int | None = Field(default=None, ge=0, le=50)
    fallback_playlist: str | None = Field(default=None, max_length=300)


@app.put("/api/admin/settings", dependencies=[Depends(require_admin)])
async def admin_settings(body: SettingsBody):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "queue_mode" in patch and patch["queue_mode"] not in ("vote", "fifo"):
        raise HTTPException(400, "queue_mode must be 'vote' or 'fifo'")
    if "skip_ratio" in patch:
        patch["skip_ratio"] = max(0.1, min(1.0, patch["skip_ratio"]))
    if "fallback_playlist" in patch:
        patch["fallback_playlist"] = patch["fallback_playlist"].strip()
    state.update_settings(patch)
    _schedule_broadcast()
    return state.settings


# ---- network: speaker allowlist + setup mode (via the root helper queue)

class MacBody(BaseModel):
    mac: str = Field(max_length=32)
    name: str = Field(default="", max_length=40)


@app.post("/api/admin/network/allow", dependencies=[Depends(require_admin)])
async def admin_network_allow(body: MacBody):
    mac = normalize_mac(body.mac)
    if not mac:
        raise HTTPException(400, "That doesn't look like a MAC address (aa:bb:cc:dd:ee:ff)")
    if not helper.allow_mac(mac, body.name):
        raise HTTPException(503, "Firewall helper is not available on this system")
    return {"ok": True}


@app.post("/api/admin/network/allow-device", dependencies=[Depends(require_admin)])
async def admin_network_allow_device(body: BanBody):
    """Allow internet for a guest device (e.g. the host's phone during setup)."""
    ip = (jam.devices.get(body.device_id) or {}).get("ip", "")
    mac = arp.mac_for(ip) if ip else None
    if not mac:
        raise HTTPException(404, "Can't find that device on the network")
    name = (jam.devices.get(body.device_id) or {}).get("name", "") or "phone"
    helper.allow_mac(mac, name)
    return {"ok": True, "mac": mac}


@app.delete("/api/admin/network/allow/{mac}", dependencies=[Depends(require_admin)])
async def admin_network_revoke(mac: str):
    norm = normalize_mac(mac)
    if not norm:
        raise HTTPException(400, "Bad MAC")
    helper.revoke_mac(norm)
    return {"ok": True}


class SetupModeBody(BaseModel):
    minutes: int = Field(default=30, ge=0, le=240)


@app.post("/api/admin/network/setup-mode", dependencies=[Depends(require_admin)])
async def admin_setup_mode(body: SetupModeBody):
    if not helper.setup_mode(body.minutes * 60):
        raise HTTPException(503, "Firewall helper is not available on this system")
    return {"ok": True}


@app.get("/api/admin/network/my-mac", dependencies=[Depends(require_admin)])
async def admin_my_mac(request: Request):
    ip = _client_ip(request)
    return {"ip": ip, "mac": arp.mac_for(ip) or ""}


# ---------------------------------------------------------------- pages

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
async def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/kiosk")
async def kiosk_page():
    return FileResponse(STATIC_DIR / "kiosk.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def run() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=config.server_port,
        access_log=False,          # the journal lives on the SD card; keep it quiet
        proxy_headers=False,       # nothing in front of us; never trust X-Forwarded-*
        server_header=False,
        limit_concurrency=200,     # a 2 GB Pi: shed load instead of swapping
        ws_max_size=16 * 1024,
        timeout_keep_alive=10,
    )


if __name__ == "__main__":
    run()
