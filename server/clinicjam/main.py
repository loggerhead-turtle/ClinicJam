"""ClinicJam web server: guest app, admin panel, captive portal, audio stream."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.websockets import WebSocket, WebSocketDisconnect

from .cast import CastController
from .config import Config, StateStore
from .events import Hub
from .jam import Jam, JamError
from .player import Player
from .spotify import SpotifyClient, SpotifyError
from .stream import AudioStreamer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("clinicjam")

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

config = Config.load()
state = StateStore(config.state_file)
jam = Jam(state.settings)
jam.banned = set(state.data.get("banned_devices", []))
spotify = SpotifyClient(config, state)
streamer = AudioStreamer(config.fifo_path, config.stream_bitrate)
stream_url = f"http://{config.ap_ip}:{config.server_port}/stream.mp3"
cast = CastController(stream_url)
cast.set_target(state.settings.get("cast_target", ""))
player = Player(jam, spotify, cast, state)
hub = Hub()

admin_sessions: set[str] = set()

# Hostnames/paths phones probe to decide whether a captive portal exists.
PORTAL_PROBE_PATHS = {
    "/generate_204", "/gen_204",                 # Android
    "/hotspot-detect.html", "/library/test/success.html",  # Apple
    "/connecttest.txt", "/ncsi.txt", "/redirect",          # Windows
    "/success.txt", "/canonical.html",                     # Firefox/NM
}


def _schedule_broadcast() -> None:
    async def _send() -> None:
        await hub.broadcast_state(lambda dev: jam.public_state(dev))
    with contextlib.suppress(RuntimeError):
        asyncio.get_running_loop().create_task(_send())


player.on_change = _schedule_broadcast


async def _lifespan(app: FastAPI):
    await streamer.start()
    await cast.start_discovery()
    await player.start()

    async def ticker() -> None:
        # Progress bars advance client-side; a slow heartbeat keeps
        # listener counts and stragglers fresh.
        while True:
            await asyncio.sleep(10)
            _schedule_broadcast()

    tick = asyncio.create_task(ticker())
    yield
    tick.cancel()
    await player.stop()
    await streamer.stop()


app = FastAPI(title="ClinicJam", lifespan=_lifespan)


# ---------------------------------------------------------------- portal

@app.middleware("http")
async def captive_portal(request: Request, call_next):
    path = request.url.path
    if path in PORTAL_PROBE_PATHS:
        # A redirect (not a 204/success body) tells the OS "captive portal
        # here" and pops the sign-in sheet, which we point at the jam.
        return RedirectResponse(f"http://{config.ap_ip}/", status_code=302)
    host = (request.headers.get("host") or "").split(":")[0]
    known = {config.ap_ip, "localhost", "127.0.0.1", "clinicjam", "clinicjam.lan", ""}
    # Only whole-page navigations get herded to the portal; API, stream,
    # and websocket traffic must never be redirected.
    is_page = not (
        path.startswith("/api") or path.startswith("/static")
        or path in ("/ws", "/stream.mp3", "/captive-portal-api")
    )
    if is_page and host not in known:
        # Guest browser aimed at some internet site the walled garden
        # blocks — bring them home to the jam instead.
        return RedirectResponse(f"http://{config.ap_ip}/", status_code=302)
    return await call_next(request)


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


# ---------------------------------------------------------------- guest API

def _device(request: Request) -> tuple[str, str]:
    device_id = request.headers.get("x-device-id", "")[:40]
    name = request.headers.get("x-device-name", "")[:24]
    if device_id:
        jam.touch_device(device_id, name)
    return device_id, name


@app.get("/api/state")
async def api_state(request: Request):
    device_id, _ = _device(request)
    return jam.public_state(device_id)


@app.get("/api/search")
async def api_search(q: str, request: Request):
    _device(request)
    q = q.strip()
    if len(q) < 2:
        return {"tracks": []}
    try:
        tracks = await spotify.search(q)
    except SpotifyError as e:
        raise HTTPException(503, f"Search unavailable: {e}")
    if state.settings.get("explicit_filter"):
        tracks = [t for t in tracks if not t.explicit]
    return {"tracks": [t.public() for t in tracks]}


class AddBody(BaseModel):
    uri: str


@app.post("/api/queue")
async def api_add(body: AddBody, request: Request):
    device_id, name = _device(request)
    if not device_id:
        raise HTTPException(400, "Missing device id")
    if not body.uri.startswith("spotify:track:"):
        raise HTTPException(400, "Not a Spotify track")
    try:
        track = await spotify.track(body.uri)
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


@app.post("/api/queue/{item_id}/vote")
async def api_vote(item_id: str, body: VoteBody, request: Request):
    device_id, _ = _device(request)
    if not device_id:
        raise HTTPException(400, "Missing device id")
    try:
        jam.vote(item_id, device_id, body.up)
    except JamError as e:
        raise HTTPException(409, str(e))
    _schedule_broadcast()
    return jam.public_state(device_id)


@app.post("/api/skip-vote")
async def api_skip_vote(request: Request):
    device_id, _ = _device(request)
    if not device_id:
        raise HTTPException(400, "Missing device id")
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
    device_id = ws.query_params.get("device", "")[:40]
    jam.touch_device(device_id)
    await hub.connect(ws, device_id)
    try:
        await ws.send_json({"type": "state", "state": jam.public_state(device_id)})
        while True:
            await ws.receive_text()  # pings keep the device 'active'
            jam.touch_device(device_id)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(ws)


# ---------------------------------------------------------------- stream

@app.get("/stream.mp3")
async def stream_mp3():
    return StreamingResponse(
        streamer.broadcaster.subscribe(),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "icy-name": "ClinicJam"},
    )


# ---------------------------------------------------------------- internal

@app.post("/api/internal/librespot-event")
async def librespot_event(request: Request):
    if request.client and request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403)
    body = await request.json()
    player.notify_librespot_event(body.get("event", ""))
    return {"ok": True}


# ---------------------------------------------------------------- admin

class LoginBody(BaseModel):
    pin: str


@app.post("/api/admin/login")
async def admin_login(body: LoginBody, response: Response):
    if not secrets.compare_digest(body.pin, config.admin_pin):
        raise HTTPException(401, "Wrong PIN")
    token = secrets.token_urlsafe(24)
    admin_sessions.add(token)
    response.set_cookie("clinicjam_admin", token, httponly=True, max_age=86400 * 30)
    return {"ok": True}


def require_admin(request: Request) -> None:
    token = request.cookies.get("clinicjam_admin", "")
    if token not in admin_sessions:
        raise HTTPException(401, "Admin login required")


@app.get("/api/admin/status", dependencies=[Depends(require_admin)])
async def admin_status():
    librespot_seen = False
    if spotify.connected:
        with contextlib.suppress(SpotifyError):
            librespot_seen = bool(await spotify.find_device())
    return {
        "spotify_connected": spotify.connected,
        "librespot_visible": librespot_seen,
        "cast": await cast.status(),
        "cast_devices": cast.devices(),
        "settings": state.settings,
        "jam_active": jam.active,
        "paused": jam.paused,
        "listeners": jam.active_device_count(),
        "banned": sorted(jam.banned),
        "devices": [
            {"id": did, "name": info["name"] or "(unnamed)"}
            for did, info in jam.devices.items()
        ],
        "last_error": player.last_error,
    }


@app.get("/api/admin/spotify/auth-url", dependencies=[Depends(require_admin)])
async def admin_auth_url():
    try:
        return {"url": spotify.auth_url()}
    except SpotifyError as e:
        raise HTTPException(400, str(e))


class CallbackBody(BaseModel):
    url: str


@app.post("/api/admin/spotify/callback", dependencies=[Depends(require_admin)])
async def admin_auth_callback(body: CallbackBody):
    try:
        await spotify.complete_auth(body.url)
    except SpotifyError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class TargetBody(BaseModel):
    uuid: str


@app.post("/api/admin/cast/target", dependencies=[Depends(require_admin)])
async def admin_cast_target(body: TargetBody):
    cast.set_target(body.uuid)
    state.update_settings({"cast_target": body.uuid})
    if jam.active:
        await cast.ensure_casting()
    return {"ok": True}


class VolumeBody(BaseModel):
    level: float
    uuid: str = ""


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
    removed = jam.remove(item_id)
    _schedule_broadcast()
    return {"removed": removed}


class BanBody(BaseModel):
    device_id: str


@app.post("/api/admin/ban", dependencies=[Depends(require_admin)])
async def admin_ban(body: BanBody):
    jam.banned.add(body.device_id)
    jam.queue = [i for i in jam.queue if i.device_id != body.device_id]
    state.data["banned_devices"] = sorted(jam.banned)
    state.save()
    _schedule_broadcast()
    return {"ok": True}


@app.delete("/api/admin/ban/{device_id}", dependencies=[Depends(require_admin)])
async def admin_unban(device_id: str):
    jam.banned.discard(device_id)
    state.data["banned_devices"] = sorted(jam.banned)
    state.save()
    return {"ok": True}


class SettingsBody(BaseModel):
    queue_mode: str | None = None
    explicit_filter: bool | None = None
    skip_ratio: float | None = None
    per_user_limit: int | None = None
    fallback_playlist: str | None = None


@app.put("/api/admin/settings", dependencies=[Depends(require_admin)])
async def admin_settings(body: SettingsBody):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "queue_mode" in patch and patch["queue_mode"] not in ("vote", "fifo"):
        raise HTTPException(400, "queue_mode must be 'vote' or 'fifo'")
    if "skip_ratio" in patch:
        patch["skip_ratio"] = max(0.1, min(1.0, patch["skip_ratio"]))
    state.update_settings(patch)
    _schedule_broadcast()
    return state.settings


# ---------------------------------------------------------------- pages

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
async def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.server_port)


if __name__ == "__main__":
    run()
