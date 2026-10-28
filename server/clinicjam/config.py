"""Configuration loading and persisted runtime state.

Static install-time settings come from a TOML file (default
/etc/clinicjam/config.toml). Settings the admin can change at runtime —
queue mode, filters, the Spotify refresh token, bans — live in a small
JSON state file so they survive restarts without editing the TOML.
"""

from __future__ import annotations

import json
import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATHS = [
    os.environ.get("CLINICJAM_CONFIG", ""),
    "/etc/clinicjam/config.toml",
    str(Path(__file__).resolve().parents[2] / "config.example.toml"),
]


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
    stream_bitrate: str = "320k"
    fifo_path: str = "/run/clinicjam/audio.fifo"
    state_file: str = "/var/lib/clinicjam/state.json"
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
            stream_bitrate=get("audio", "stream_bitrate", "320k"),
            fifo_path=get("audio", "fifo_path", "/run/clinicjam/audio.fifo"),
            state_file=get("paths", "state_file", "/var/lib/clinicjam/state.json"),
            raw=raw,
        )


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
    """Tiny JSON persistence for tokens, settings, and bans."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "spotify_refresh_token": "",
            "settings": dict(DEFAULT_SETTINGS),
            "banned_devices": [],
        }
        self._load()

    def _load(self) -> None:
        try:
            on_disk = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return
        self.data.update(on_disk)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(self.data.get("settings", {}))
        self.data["settings"] = merged

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        # This file holds the Spotify refresh token — owner-only, belt and
        # suspenders alongside the install-time directory permissions.
        tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(self.path)

    @property
    def settings(self) -> dict[str, Any]:
        return self.data["settings"]

    def update_settings(self, patch: dict[str, Any]) -> None:
        for key, value in patch.items():
            if key in DEFAULT_SETTINGS:
                self.data["settings"][key] = value
        self.save()
