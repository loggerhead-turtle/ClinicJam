#!/bin/bash
# Configures the Pi's OWN internet uplink per [uplink] in config.toml.
# Safe to re-run any time you change uplink settings:
#   sudo /opt/clinicjam/install/setup-uplink.sh
#
# This has nothing to do with the guest jam Wi-Fi (hostapd/dnsmasq handle
# that separately) — this is only how the Pi itself reaches Spotify and
# lets the Nest speakers check in with Google.
#
# The connection is written as a NetworkManager keyfile (not via nmcli)
# so it also works at first boot before NetworkManager is running.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }

CONFIG=/etc/clinicjam/config.toml
CONN=/etc/NetworkManager/system-connections/clinicjam-uplink.nmconnection

get() {
    python3 - "$1" <<'PYEOF'
import sys, tomllib
with open("/etc/clinicjam/config.toml", "rb") as f:
    cfg = tomllib.load(f)
node = cfg
for part in sys.argv[1].split("."):
    node = node.get(part, {}) if isinstance(node, dict) else {}
print(node if isinstance(node, (str, bool)) else "")
PYEOF
}

nm_running() { systemctl -q is-active NetworkManager 2>/dev/null; }

MODE="$(get uplink.mode)"
MODE="${MODE:-cellular}"
echo "==> uplink mode: $MODE"

case "$MODE" in
  cellular|ethernet|"")
    rm -f "$CONN"
    nm_running && nmcli connection reload >/dev/null 2>&1 || true
    echo "Nothing to configure — NetworkManager brings up the modem/ethernet"
    echo "on its own. Check status with: nmcli device"
    ;;

  wifi_client)
    IFACE="$(get uplink.wifi_client.interface)"; IFACE="${IFACE:-wlan1}"
    SSID="$(get uplink.wifi_client.ssid)"
    PSK="$(get uplink.wifi_client.psk)"
    AP_IF="$(get network.ap_interface)"; AP_IF="${AP_IF:-wlan0}"

    [ -n "$SSID" ] || { echo "xx uplink.wifi_client.ssid is empty in config.toml — set it and re-run"; exit 1; }
    if [ "$IFACE" = "$AP_IF" ]; then
        echo "xx uplink.wifi_client.interface ($IFACE) must be different from"
        echo "   network.ap_interface ($AP_IF) — they need two separate radios"
        echo "   (the Pi's built-in one hosts the jam Wi-Fi; a USB dongle joins"
        echo "   the office network)."
        exit 1
    fi
    if ! ip link show "$IFACE" >/dev/null 2>&1; then
        echo "!! interface $IFACE not found right now. Is the USB Wi-Fi dongle plugged in?"
        echo "   The connection is saved anyway and will come up when it appears."
    fi
    [ -n "$PSK" ] || echo "!! no password set for '$SSID': the Pi will join ANY network with that name (evil-twin risk). Prefer a WPA2 network."

    echo "==> Saving connection to '$SSID' on $IFACE (NetworkManager keyfile)..."
    UUID=$(cat /proc/sys/kernel/random/uuid)
    mkdir -p "$(dirname "$CONN")"
    {
        cat <<EOF
[connection]
id=clinicjam-uplink
uuid=$UUID
type=wifi
interface-name=$IFACE
autoconnect=true
autoconnect-priority=10
autoconnect-retries=0

[wifi]
mode=infrastructure
ssid=$SSID

[ipv4]
method=auto
route-metric=50
# never let the office network push us a default DNS for the jam side
ignore-auto-dns=false

[ipv6]
method=disabled

[proxy]
EOF
        if [ -n "$PSK" ]; then
            cat <<EOF

[wifi-security]
key-mgmt=wpa-psk
psk=$PSK
EOF
        fi
    } > "$CONN"
    chmod 600 "$CONN"
    if nm_running; then
        nmcli connection reload >/dev/null 2>&1 || true
        nmcli connection up clinicjam-uplink >/dev/null 2>&1 \
            && echo "==> Joined '$SSID'." \
            || echo "!! not connected yet — NetworkManager keeps retrying in the background (nmcli device)"
    else
        echo "==> Saved; NetworkManager will connect when it starts."
    fi

    PORTAL_ENABLED="$(get uplink.captive_portal.enabled)"
    if [ "$PORTAL_ENABLED" = "True" ]; then
        if [ ! -f /etc/clinicjam/portal_click.py ]; then
            echo
            echo "!! uplink.captive_portal.enabled = true but"
            echo "   /etc/clinicjam/portal_click.py doesn't exist yet. The company"
            echo "   Wi-Fi's captive portal will block real internet access until"
            echo "   you set this up:"
            echo "     cp /opt/clinicjam/docs/portal_click.example.py /etc/clinicjam/portal_click.py"
            echo "   See docs/NETWORK.md for how to capture your portal's exact request."
        fi
        nm_running && systemctl restart clinicjam-portal-watch 2>/dev/null || true
    fi
    ;;

  *)
    echo "xx unknown uplink.mode '$MODE' in config.toml — expected 'cellular', 'ethernet', or 'wifi_client'"
    exit 1
    ;;
esac
