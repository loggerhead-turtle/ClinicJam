#!/usr/bin/env python3
"""Apply the plain-text settings file from the SD card's boot partition.

The ready-made image puts `clinicjam-settings.txt` on the boot partition
(the small drive that shows up on a laptop after flashing). It is the
*only* thing a non-technical host has to edit: Wi-Fi name/password, PINs,
the office Wi-Fi the Pi should use for internet, and the login password.

`clinicjam-firstboot.service` runs this at every boot. If the file changed
since last time, its values are written into /etc/clinicjam/config.toml,
the network configs are re-rendered, the uplink is (re)configured and the
services restarted. A short report is written next to it as
`clinicjam-status.txt` so a headless Pi can still tell you what it did.

Also usable by hand:   apply_settings.py --set admin_pin=123456 ...
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path

CONFIG = Path("/etc/clinicjam/config.toml")
APPLIED = Path("/var/lib/clinicjam/.settings-applied")
BOOT_DIRS = [Path("/boot/firmware"), Path("/boot")]
SETTINGS_NAME = "clinicjam-settings.txt"
STATUS_NAME = "clinicjam-status.txt"

# settings-file key -> (toml section, toml key, kind)
KEYS = {
    "jam_wifi_name": ("network", "ssid", "str"),
    "jam_wifi_password": ("network", "wpa2_passphrase", "str"),
    "country": ("network", "country", "str"),
    "wifi_band": ("network", "band", "str"),
    "client_isolation": ("network", "client_isolation", "bool"),
    "ssh_from_jam_wifi": ("network", "ssh_from_jam_wifi", "bool"),
    "admin_pin": ("server", "admin_pin", "str"),
    "guest_pin": ("server", "guest_pin", "str"),
    "office_wifi_name": ("uplink.wifi_client", "ssid", "str"),
    "office_wifi_password": ("uplink.wifi_client", "psk", "str"),
    "spotify_client_id": ("spotify", "client_id", "str"),
    "spotify_client_secret": ("spotify", "client_secret", "str"),
}
TRUE_WORDS = {"1", "true", "yes", "on", "y"}


def log(msg: str) -> None:
    print(f"clinicjam-settings: {msg}", flush=True)


# ---------------------------------------------------------------- parsing

def parse_settings(text: str) -> dict[str, str]:
    """`key = value` lines; `#` comments; optional quotes around values."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower().replace("-", "_").replace(" ", "_")
        value = value.strip()
        # strip an inline comment only if it is preceded by whitespace
        value = re.split(r"\s+#", value, 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def validate(settings: dict[str, str]) -> list[str]:
    problems = []
    ssid = settings.get("jam_wifi_name")
    if ssid is not None and not (1 <= len(ssid.encode()) <= 32):
        problems.append("jam_wifi_name must be 1-32 characters")
    pw = settings.get("jam_wifi_password")
    if pw and not (8 <= len(pw) <= 63):
        problems.append("jam_wifi_password must be 8-63 characters (or empty for an open network)")
    opw = settings.get("office_wifi_password")
    if opw and not (8 <= len(opw) <= 63):
        problems.append("office_wifi_password must be 8-63 characters")
    pin = settings.get("admin_pin")
    if pin is not None and (len(pin) < 6 or len(pin) > 64):
        problems.append("admin_pin must be at least 6 characters")
    gp = settings.get("guest_pin")
    if gp and len(gp) > 64:
        problems.append("guest_pin is too long")
    c = settings.get("country")
    if c is not None and not re.fullmatch(r"[A-Za-z]{2}", c):
        problems.append("country must be a two-letter code like US")
    band = settings.get("wifi_band")
    if band is not None and band not in ("2.4", "5"):
        problems.append("wifi_band must be 2.4 or 5")
    lp = settings.get("login_password")
    if lp is not None and lp and len(lp) < 8:
        problems.append("login_password must be at least 8 characters")
    return problems


# ---------------------------------------------------------------- TOML editing
#
# config.toml is our own file with a known, flat shape (sections of
# scalar keys). We edit it textually so the explanatory comments survive.

def toml_literal(value: object, kind: str) -> str:
    if kind == "bool":
        return "true" if value else "false"
    if kind == "int":
        return str(int(value))
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def set_key(text: str, section: str, key: str, literal: str) -> str:
    """Set `key = literal` inside [section], adding either if missing."""
    header = f"[{section}]"
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == header), None)
    if start is None:
        lines += ["", header, f"{key} = {literal}"]
        return "\n".join(lines) + "\n"
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^\s*\[", lines[i]):
            end = i
            break
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i in range(start + 1, end):
        if pat.match(lines[i]):
            lines[i] = f"{key} = {literal}"
            return "\n".join(lines) + "\n"
    # Insert before the trailing blank lines of the section.
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, f"{key} = {literal}")
    return "\n".join(lines) + "\n"


def apply_to_config(settings: dict[str, str]) -> list[str]:
    text = CONFIG.read_text()
    changed = []
    for skey, (section, tkey, kind) in KEYS.items():
        if skey not in settings:
            continue
        value: object = settings[skey]
        if kind == "bool":
            value = str(value).strip().lower() in TRUE_WORDS
        text = set_key(text, section, tkey, toml_literal(value, kind))
        changed.append(skey)
    # Uplink mode follows from whether an office Wi-Fi was given.
    if "office_wifi_name" in settings:
        mode = "wifi_client" if settings["office_wifi_name"].strip() else "ethernet"
        text = set_key(text, "uplink", "mode", toml_literal(mode, "str"))
    tomllib.loads(text)  # must still parse
    tmp = CONFIG.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    try:
        import grp
        os.chown(tmp, 0, grp.getgrnam("clinicjam").gr_gid)
    except (KeyError, OSError):
        pass
    os.replace(tmp, CONFIG)
    return changed


# ---------------------------------------------------------------- system bits

def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    log("$ " + shlex.join(cmd))
    return subprocess.run(cmd, check=False, capture_output=True, text=True, **kw)


def set_country(country: str) -> None:
    country = country.upper()
    crda = Path("/etc/default/crda")
    if crda.exists():
        txt = crda.read_text()
        txt = re.sub(r"^REGDOMAIN=.*$", f"REGDOMAIN={country}", txt, flags=re.M)
        crda.write_text(txt)
    run(["iw", "reg", "set", country])
    run(["rfkill", "unblock", "all"])
    for f in Path("/var/lib/systemd/rfkill").glob("*:wlan"):
        try:
            f.write_text("0\n")
        except OSError:
            pass


def set_login_password(password: str) -> None:
    """A login password turns SSH on; no password keeps it off."""
    systemd = Path("/run/systemd/system").is_dir()
    if not password:
        if systemd:
            run(["systemctl", "disable", "--now", "ssh"])
        log("no login_password: SSH stays off")
        return
    try:
        import pwd
        user = next(p.pw_name for p in pwd.getpwall() if p.pw_uid == 1000)
    except StopIteration:
        log("no uid-1000 user yet; login password not changed")
        return
    subprocess.run(["chpasswd"], input=f"{user}:{password}\n", text=True, check=False)
    if systemd:
        run(["systemctl", "enable", "--now", "ssh"])
    log(f"login password set for user '{user}'; SSH enabled")


def configure_uplink() -> None:
    run(["/opt/clinicjam/install/setup-uplink.sh"])


def restart_services() -> None:
    run(["python3", "/opt/clinicjam/bin/render_network.py"])
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "restart", "clinicjam-network", "clinicjam-firewall",
         "clinicjam-hostapd", "clinicjam-dnsmasq", "clinicjam-dns-nest",
         "clinicjam-web", "clinicjam-librespot", "clinicjam-portal-watch"])


def write_status(boot_dir: Path, lines: list[str]) -> None:
    try:
        (boot_dir / STATUS_NAME).write_text("\n".join(lines) + "\n")
    except OSError as e:
        log(f"could not write status file: {e}")


# ---------------------------------------------------------------- main

def apply_file(boot_dir: Path, path: Path) -> int:
    text = path.read_text(errors="replace")
    digest = hashlib.sha256(text.encode()).hexdigest()
    try:
        if APPLIED.read_text().strip() == digest:
            log("settings unchanged; nothing to do")
            return 0
    except OSError:
        pass

    settings = parse_settings(text)
    problems = validate(settings)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if problems:
        log("settings file has problems: " + "; ".join(problems))
        write_status(boot_dir, [
            f"ClinicJam could NOT apply {SETTINGS_NAME} at {stamp}:",
            *("  - " + p for p in problems),
            "Fix the file, put the card back in the Pi, and boot again.",
        ])
        return 1

    changed = apply_to_config(settings)
    if settings.get("country"):
        set_country(settings["country"])
    if "login_password" in settings:
        set_login_password(settings["login_password"])
    if Path("/run/systemd/system").is_dir():
        configure_uplink()
        restart_services()

    APPLIED.parent.mkdir(parents=True, exist_ok=True)
    APPLIED.write_text(digest + "\n")
    cfg = tomllib.loads(CONFIG.read_text())
    ap_ip = cfg.get("network", {}).get("ap_ip", "10.24.7.1")
    ssid = cfg.get("network", {}).get("ssid", "ClinicJam")
    write_status(boot_dir, [
        f"ClinicJam applied {SETTINGS_NAME} at {stamp}. Settings taken: {', '.join(changed) or 'none'}",
        "",
        f"  Jam Wi-Fi:     {ssid}",
        f"  Guest app:     http://{ap_ip}/",
        f"  Host panel:    http://{ap_ip}/admin",
        f"  Office Wi-Fi:  {settings.get('office_wifi_name') or '(not set — using Ethernet/modem)'}",
        f"  SSH:           {'on (user admin)' if settings.get('login_password') else 'off (set login_password to turn it on)'}",
        "",
        "You can edit the settings file again at any time; it is re-applied on the next boot.",
    ])
    log(f"applied: {', '.join(changed)}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "--set":
        settings = parse_settings("\n".join(argv[2:]))
        problems = validate(settings)
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 1
        # Nobody should ship a public default PIN: generate one if missing.
        if "admin_pin" in settings and not settings["admin_pin"]:
            settings["admin_pin"] = "".join(secrets.choice("0123456789") for _ in range(6))
            print(f"Generated admin PIN: {settings['admin_pin']}  (write it down!)")
        apply_to_config(settings)
        if settings.get("country"):
            set_country(settings["country"])
        return 0

    for boot_dir in BOOT_DIRS:
        path = boot_dir / SETTINGS_NAME
        if path.is_file():
            return apply_file(boot_dir, path)
    log("no settings file on the boot partition; nothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
