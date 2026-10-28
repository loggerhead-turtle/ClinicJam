"""Local network facts (IP -> MAC) and the privileged-helper queue.

The web service runs as an unprivileged user with no sudo. Anything that
needs root — adding a speaker's MAC to the firewall allowlist, opening
"setup mode", restarting the Spotify player — is written as a tiny
request file into /run/clinicjam/fw-queue. A root-owned systemd path
unit (clinicjam-fw.path -> clinicjam-fw.service) notices the directory
filling up, validates each request strictly, applies it, and writes the
resulting state to /run/clinicjam/fw-state.json for us to read back.

No shell-outs, no sudo, no privileges in this process.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
ARP_TABLE = Path("/proc/net/arp")


def normalize_mac(mac: str) -> str | None:
    mac = mac.strip().lower().replace("-", ":")
    return mac if MAC_RE.match(mac) else None


class ArpCache:
    """IP -> MAC from the kernel neighbour table, cached a few seconds."""

    def __init__(self, ttl: float = 5.0):
        self.ttl = ttl
        self._at = 0.0
        self._table: dict[str, str] = {}

    def _refresh(self) -> None:
        table: dict[str, str] = {}
        try:
            lines = ARP_TABLE.read_text().splitlines()[1:]
        except OSError:
            lines = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 4:
                mac = normalize_mac(parts[3])
                if mac and mac != "00:00:00:00:00:00":
                    table[parts[0]] = mac
        self._table = table
        self._at = time.monotonic()

    def mac_for(self, ip: str) -> str | None:
        if time.monotonic() - self._at > self.ttl:
            self._refresh()
        return self._table.get(ip)


class HelperQueue:
    def __init__(self, run_dir: str = "/run/clinicjam"):
        self.queue_dir = Path(run_dir) / "fw-queue"
        self.state_file = Path(run_dir) / "fw-state.json"

    @property
    def available(self) -> bool:
        return self.queue_dir.is_dir() and os.access(self.queue_dir, os.W_OK)

    def _submit(self, request: dict[str, Any]) -> bool:
        if not self.available:
            log.warning("helper queue unavailable (%s); dropping %s", self.queue_dir, request)
            return False
        name = f"{int(time.time())}-{secrets.token_hex(4)}.json"
        tmp = self.queue_dir / (name + ".tmp")
        try:
            tmp.write_text(json.dumps(request))
            tmp.replace(self.queue_dir / name)
            return True
        except OSError as e:
            log.warning("cannot queue helper request: %s", e)
            return False

    # ---- requests -------------------------------------------------------

    def allow_mac(self, mac: str, name: str = "") -> bool:
        mac = normalize_mac(mac) or ""
        if not mac:
            return False
        return self._submit({"op": "allow", "mac": mac, "name": name[:40]})

    def revoke_mac(self, mac: str) -> bool:
        mac = normalize_mac(mac) or ""
        if not mac:
            return False
        return self._submit({"op": "revoke", "mac": mac})

    def setup_mode(self, seconds: int) -> bool:
        seconds = max(0, min(int(seconds), 4 * 3600))
        return self._submit({"op": "setup-mode", "seconds": seconds})

    def restart_player(self) -> bool:
        return self._submit({"op": "restart-librespot"})

    # ---- state ----------------------------------------------------------

    def state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_file.read_text())
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {"allowed": {}, "setup_mode_until": 0}
