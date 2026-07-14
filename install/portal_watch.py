#!/usr/bin/env python3
"""Keeps the Pi's own uplink connection past a captive portal unattended.

Only relevant when [uplink] mode = "wifi_client" in config.toml — i.e. the
Pi joins an existing Wi-Fi network (like a company's guest network) as a
client through a second radio, rather than a cellular modem or Ethernet.
Some such networks intercept the first request with a "click to accept
terms" splash page and refuse real internet until that's accepted; this
loop notices that state and replays the site-specific accept sequence
from /etc/clinicjam/portal_click.py, so no human ever has to click it.

This has nothing to do with the guest-facing captive portal on the jam
Wi-Fi (that one is intentional and stays — see clinicjam/main.py). This
script is about the Pi's OWN outbound connection.

If captive_portal.enabled is false (the default), this idles forever and
does nothing — harmless to leave the systemd service enabled.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("portal-watch")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

CONFIG_PATH = Path("/etc/clinicjam/config.toml")
CLICK_SCRIPT = Path("/etc/clinicjam/portal_click.py")
IDLE_SECONDS = 3600


def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def is_online(probe_url: str, timeout: float = 8.0) -> bool:
    """A captive portal, by definition, intercepts this and turns it into
    something other than a bare 204 — a redirect, a 200 with an HTML body,
    or a connection to a different host entirely. Real internet is the
    only case that passes the probe untouched."""
    try:
        with urllib.request.urlopen(probe_url, timeout=timeout) as resp:
            body = resp.read(512)
            return resp.status == 204 and not body
    except urllib.error.HTTPError as e:
        log.debug("probe HTTP error: %s", e)
        return False
    except Exception as e:
        log.warning("connectivity probe failed (%s) — treating as offline", e)
        return False


def run_click_script() -> bool:
    if not CLICK_SCRIPT.is_file():
        log.error(
            "captive_portal.enabled is true but %s doesn't exist — copy "
            "docs/portal_click.example.py there and customize it for your "
            "portal (see docs/NETWORK.md).",
            CLICK_SCRIPT,
        )
        return False
    try:
        result = subprocess.run(
            [sys.executable, str(CLICK_SCRIPT)],
            timeout=30,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            log.warning(
                "portal_click.py exited %s: %s",
                result.returncode,
                result.stderr.strip()[:400],
            )
            return False
        log.info("portal_click.py ran successfully")
        return True
    except Exception:
        log.exception("portal_click.py crashed")
        return False


def main() -> None:
    cfg = load_config()
    uplink = cfg.get("uplink", {})
    portal = uplink.get("captive_portal", {})

    if uplink.get("mode") != "wifi_client" or not portal.get("enabled"):
        log.info("captive-portal automation not enabled — idling")
        while True:
            time.sleep(IDLE_SECONDS)

    interval = float(portal.get("check_interval_seconds", 120))
    probe_url = portal.get(
        "probe_url", "http://connectivitycheck.gstatic.com/generate_204"
    )
    log.info("watching uplink connectivity every %ss via %s", interval, probe_url)

    while True:
        if is_online(probe_url):
            log.debug("uplink online")
        else:
            log.info("uplink looks blocked by the captive portal — auto-accepting")
            if run_click_script():
                time.sleep(5)
                if is_online(probe_url):
                    log.info("back online after auto-accept")
                else:
                    log.warning("still offline after auto-accept — retrying next cycle")
        time.sleep(interval)


if __name__ == "__main__":
    main()
