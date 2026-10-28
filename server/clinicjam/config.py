"""Configuration loading and persisted runtime state.

Static install-time settings come from a TOML file (default
/etc/clinicjam/config.toml). Settings the admin can change at runtime —
queue mode, filters, the Spotify refresh token, bans — live in a small
JSON state file so they survive restarts without editing the TOML.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATHS = [
    os.environ.get("CLINICJAM_CONFIG", ""),
    "/etc/clinicjam/config.toml",
    str(Path(__file__).resolve().parents[2] / "config.example.toml"),
]

# Spotify's own desktop client id. librespot uses it for its OAuth login
# (with PKCE, so no secret is involved). Using the same token for the Web
# API means nobody has to create a developer app to get started.
DEFAULT_PLAYER_CLIENT_ID = "65b708073fc0480ea92a077233ca87bd"
PLAYER_REDIRECT_URI = "http://127.0.0.1:8898/login"

WEAK_PINS = {"", "0000", "1111", "1234", "2468", "1357", "123456", "000000", "111111"}


@dataclass
class Config:
    ap_interface: str = "wlan0"
    ap_ip: str = "10.24.7.1"
    ssid: str = "ClinicJam"
    server_port: int = 8080
    admin_pin: str = "2468"
    guest_pin: str = ""
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8888/callback"
    spotify_device_name: str = "ClinicJam"
    player_client_id: str = DEFAULT_PLAYER_CLIENT_ID
    stream_bitrate: str = "320k"
    fifo_path: str = "/run/clinicjam/audio.fifo"
    run_dir: str = "/run/clinicjam"
    state_file: str = "/var/lib/clinicjam/state.json"
    librespot_token_file: str = "/var/lib/clinicjam/librespot-token"
    librespot_cache_dir: str = "/var/lib/clinicjam/librespot-cache"
    max_stream_clients: int = 6
    auto_allow_cast_devices: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        for candidate in DEFAULT_CONFIG_PATHS:
            if candidate and Path(candidate).is_file():
                with open(candidate, "rb") as f:
                    raw = tomllib.load(f)
                break
        else:
            raw = {}

        def get(section: str, key: str, default: Any) -> Any:
            return raw.get(section, {}).get(key, default)

        fifo = get("audio", "fifo_path", "/run/clinicjam/audio.fifo")
        return cls(
            ap_interface=get("network", "ap_interface", "wlan0"),
            ap_ip=get("network", "ap_ip", "10.24.7.1"),
            ssid=get("network", "ssid", "ClinicJam"),
            server_port=int(get("server", "port", 8080)),
            admin_pin=str(get("server", "admin_pin", "2468")),
            guest_pin=str(get("server", "guest_pin", "")),
            spotify_client_id=get("spotify", "client_id", ""),
            spotify_client_secret=get("spotify", "client_secret", ""),
            spotify_redirect_uri=get(
                "spotify", "redirect_uri", "http://127.0.0.1:8888/callback"
            ),
            spotify_device_name=get("spotify", "device_name", "ClinicJam"),
            player_client_id=get("spotify", "player_client_id", DEFAULT_PLAYER_CLIENT_ID),
            stream_bitrate=get("audio", "stream_bitrate", "320k"),
            fifo_path=fifo,
            run_dir=get("paths", "run_dir", str(Path(fifo).parent)),
            state_file=get("paths", "state_file", "/var/lib/clinicjam/state.json"),
            librespot_token_file=get(
                "paths", "librespot_token_file", "/var/lib/clinicjam/librespot-token"
            ),
            librespot_cache_dir=get(
                "paths", "librespot_cache_dir", "/var/lib/clinicjam/librespot-cache"
            ),
            max_stream_clients=int(get("audio", "max_stream_clients", 6)),
            auto_allow_cast_devices=bool(get("server", "auto_allow_cast_devices", True)),
            raw=raw,
        )

    @property
    def uses_dev_app(self) -> bool:
        """True when a Spotify developer app is configured for the Web API."""
        return bool(self.spotify_client_id and self.spotify_client_secret)

    def warnings(self) -> list[str]:
        out = []
        if self.admin_pin in WEAK_PINS or len(self.admin_pin) < 6:
            out.append(
                "admin_pin is weak — use at least 6 digits (edit "
                "/etc/clinicjam/config.toml or the settings file on the SD card)"
            )
        if self.guest_pin and self.guest_pin == self.admin_pin:
            out.append("guest_pin equals admin_pin — guests have full control")
        return out


DEFAULT_SETTINGS = {
    # "vote" reorders the queue by upvotes; "fifo" is strict first-come.
    "queue_mode": "vote",
    # Hide tracks Spotify marks explicit from search and reject direct adds.
    "explicit_filter": True,
    # Fraction of active listeners whose skip votes end the current song.
    "skip_ratio": 0.5,
    # Max pending songs per device; 0 disables the limit.
    "per_user_limit": 0,
    # Spotify playlist URI/URL to draw from when the guest queue is empty.
    "fallback_playlist": "",
    # UUID of the selected Chromecast target (ideally the speaker group).
    "cast_target": "",
}


class StateStore:
    """Tiny JSON persistence for tokens, settings, and bans.

    Written atomically with fsync so a power cut mid-write can never leave
    a truncated file (which would silently drop the Spotify link).
    The file is created 0600: it holds OAuth refresh tokens.
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "spotify_refresh_token": "",
            "spotify_token_kind": "",      # "dev_app" or "pkce"
            "session_secret": "",
            "settings": dict(DEFAULT_SETTINGS),
            "banned_devices": [],
            "banned_macs": [],
        }
        self._load()
        if not self.data.get("session_secret"):
            self.data["session_secret"] = secrets.token_hex(32)
            self.save()

    def _load(self) -> None:
        for candidate in (self.path, self.path.with_suffix(".bak")):
            try:
                on_disk = json.loads(candidate.read_text())
                break
            except FileNotFoundError:
                continue
            except (OSError, json.JSONDecodeError) as e:
                log.warning("state file %s unreadable (%s); trying backup", candidate, e)
                continue
        else:
            return
        if not isinstance(on_disk, dict):
            return
        self.data.update(on_disk)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(self.data.get("settings") or {})
        self.data["settings"] = merged

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if self.path.exists():
            try:
                os.replace(self.path, self.path.with_suffix(".bak"))
            except OSError:
                pass
        os.replace(tmp, self.path)
        try:
            dfd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass

    @property
    def settings(self) -> dict[str, Any]:
        return self.data["settings"]

    @property
    def session_secret(self) -> bytes:
        return bytes.fromhex(self.data["session_secret"])

    def update_settings(self, patch: dict[str, Any]) -> None:
        for key, value in patch.items():
            if key in DEFAULT_SETTINGS:
                self.data["settings"][key] = value
        self.save()
