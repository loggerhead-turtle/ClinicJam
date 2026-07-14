#!/bin/bash
# Configures the Pi's OWN internet uplink per [uplink] in config.toml.
# Safe to re-run any time you change uplink settings:
#   sudo /opt/clinicjam/install/setup-uplink.sh
#
# This has nothing to do with the guest jam Wi-Fi (hostapd/dnsmasq handle
# that separately) — this is only how the Pi itself reaches Spotify and
# lets the Nest speakers check in with Google.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }

CONFIG=/etc/clinicjam/config.toml

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

MODE="$(get uplink.mode)"
MODE="${MODE:-cellular}"
echo "==> uplink mode: $MODE"

case "$MODE" in
  cellular|ethernet|"")
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
        echo "   the company network)."
        exit 1
    fi
    if ! ip link show "$IFACE" >/dev/null 2>&1; then
        echo "xx interface $IFACE not found. Is the USB Wi-Fi dongle plugged in?"
        echo "   Run 'ip link' to see what interfaces actually exist, then set"
        echo "   uplink.wifi_client.interface in config.toml to match."
        exit 1
    fi

    echo "==> Joining '$SSID' on $IFACE (managed by NetworkManager)..."
    nmcli connection delete clinicjam-uplink >/dev/null 2>&1 || true
    if [ -n "$PSK" ]; then
        nmcli connection add type wifi ifname "$IFACE" con-name clinicjam-uplink \
            ssid "$SSID" \
            wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK" \
            connection.autoconnect yes connection.autoconnect-priority 10 >/dev/null
    else
        nmcli connection add type wifi ifname "$IFACE" con-name clinicjam-uplink \
            ssid "$SSID" \
            connection.autoconnect yes connection.autoconnect-priority 10 >/dev/null
    fi
    nmcli connection up clinicjam-uplink
    echo "==> Joined. NetworkManager will auto-reconnect this on boot/roam."

    PORTAL_ENABLED="$(get uplink.captive_portal.enabled)"
    if [ "$PORTAL_ENABLED" = "True" ]; then
        if [ ! -f /etc/clinicjam/portal_click.py ]; then
            echo
            echo "!! uplink.captive_portal.enabled = true but"
            echo "   /etc/clinicjam/portal_click.py doesn't exist yet. The company"
            echo "   Wi-Fi's captive portal will block real internet access until"
            echo "   you set this up:"
            echo "     cp /opt/clinicjam/docs/portal_click.example.py /etc/clinicjam/portal_click.py"
            echo "     nano /etc/clinicjam/portal_click.py     # customize for your portal"
            echo "   See docs/NETWORK.md for how to capture your portal's exact request."
        fi
        systemctl restart clinicjam-portal-watch 2>/dev/null || true
    fi
    ;;

  *)
    echo "xx unknown uplink.mode '$MODE' in config.toml — expected 'cellular', 'ethernet', or 'wifi_client'"
    exit 1
    ;;
esac
