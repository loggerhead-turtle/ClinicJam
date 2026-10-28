#!/bin/bash
# ClinicJam installer for Raspberry Pi OS Bookworm (Pi 4/5, 64-bit).
#
# One-line install:
#   curl -fsSL https://raw.githubusercontent.com/loggerhead-turtle/ClinicJam/claude/office-clinic-jam-app-voejwh/install/install.sh | sudo bash
#
# Or from a clone:
#   sudo ./install/install.sh
#
# What it does:
#   1. Installs packages (hostapd, dnsmasq, nftables, ffmpeg, python, librespot)
#   2. Creates the walled-garden Wi-Fi network with captive portal
#   3. Installs the ClinicJam server + systemd services
#
# Safe to re-run: it upgrades in place and keeps /etc/clinicjam/config.toml.

set -euo pipefail

REPO_URL="https://github.com/loggerhead-turtle/ClinicJam.git"
APP_DIR=/opt/clinicjam
CONFIG_DIR=/etc/clinicjam
DATA_DIR=/var/lib/clinicjam

say()  { echo -e "\e[1;32m==>\e[0m $*"; }
warn() { echo -e "\e[1;33m!!\e[0m $*"; }
die()  { echo -e "\e[1;31mxx\e[0m $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "Run with sudo: sudo bash install.sh"
command -v apt-get >/dev/null || die "This installer targets Raspberry Pi OS (Debian)."

# ------------------------------------------------------------------ packages
say "Installing packages (this can take a few minutes)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    hostapd dnsmasq nftables ffmpeg curl git rfkill \
    python3 python3-venv python3-pip > /dev/null

# Stock hostapd/dnsmasq services would fight with our dedicated units.
systemctl disable --now hostapd dnsmasq 2>/dev/null || true
systemctl unmask hostapd 2>/dev/null || true

# librespot: the raspotify package ships a well-built ARM binary.
if ! command -v librespot >/dev/null; then
    say "Installing librespot (via the raspotify package)..."
    curl -fsSL https://dtcooper.github.io/raspotify/install.sh | sh
    # We run librespot ourselves; raspotify's own service must not grab it.
    systemctl disable --now raspotify 2>/dev/null || true
fi
command -v librespot >/dev/null || die "librespot did not install — see https://github.com/dtcooper/raspotify"

# ------------------------------------------------------------------ code
if [ ! -d "$APP_DIR/.git" ]; then
    say "Fetching ClinicJam into $APP_DIR..."
    # Allow installing from a local checkout (dev) or from GitHub (one-liner).
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-/dev/null}")" 2>/dev/null && pwd || true)"
    if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/../server/requirements.txt" ]; then
        mkdir -p "$APP_DIR"
        cp -a "$SCRIPT_DIR/.." -T "$APP_DIR"
    else
        git clone --depth 1 "$REPO_URL" "$APP_DIR"
    fi
else
    say "Updating ClinicJam in $APP_DIR..."
    git -C "$APP_DIR" pull --ff-only || warn "git pull failed; continuing with existing code"
fi

# ------------------------------------------------------------------ user + dirs
id clinicjam >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin clinicjam
mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$APP_DIR/bin"
chown -R clinicjam:clinicjam "$DATA_DIR"
# $DATA_DIR holds the Spotify refresh token and librespot's cache. Owner
# (clinicjam) only — Unix permissions on a directory block even listing
# what's inside it to anyone else, regardless of the files' own modes.
chmod 700 "$DATA_DIR"

# ------------------------------------------------------------------ config
validate_pin() {
    # Keep PINs to what the on-screen numeric pads (phones/kiosk) can
    # enter: digits only, 4-8 of them.
    [[ "$1" =~ ^[0-9]{4,8}$ ]]
}

if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    say "Creating $CONFIG_DIR/config.toml..."
    cp "$APP_DIR/config.example.toml" "$CONFIG_DIR/config.toml"

    if [ -t 0 ]; then
        read -rp "Wi-Fi network name for guests [ClinicJam]: " SSID
        SSID=${SSID:-ClinicJam}

        PIN=""
        while ! validate_pin "$PIN"; do
            read -rp "Admin PIN for the host panel, 4-8 digits [2468]: " PIN
            PIN=${PIN:-2468}
            validate_pin "$PIN" || echo "   Digits only, 4-8 of them — try again."
        done

        GUESTPIN=""
        read -rp "Jam PIN guests enter to join, 4-8 digits (blank = no PIN) [1357]: " GUESTPIN
        GUESTPIN=${GUESTPIN-1357}
        while [ -n "$GUESTPIN" ] && ! validate_pin "$GUESTPIN"; do
            read -rp "   Digits only, 4-8 of them (or blank for no PIN): " GUESTPIN
        done

        read -rp "Two-letter Wi-Fi country code [US]: " COUNTRY
        COUNTRY=${COUNTRY:-US}

        echo
        echo "  A password-protected jam Wi-Fi keeps the PIN you just set (and"
        echo "  everything else on that network) from being readable by anyone"
        echo "  nearby with a Wi-Fi sniffer — an open network sends everything,"
        echo "  PINs included, in the clear over the air. WPA2 is recommended."
        read -rp "Wi-Fi password for guests (blank = open network, not recommended): " WIFIPASS

        sed -i "s/^ssid = .*/ssid = \"${SSID//\//\\/}\"/" "$CONFIG_DIR/config.toml"
        sed -i "s/^admin_pin = .*/admin_pin = \"${PIN//\//\\/}\"/" "$CONFIG_DIR/config.toml"
        sed -i "s/^guest_pin = .*/guest_pin = \"${GUESTPIN//\//\\/}\"/" "$CONFIG_DIR/config.toml"
        sed -i "s/^country = .*/country = \"${COUNTRY//\//\\/}\"/" "$CONFIG_DIR/config.toml"
        if [ -n "$WIFIPASS" ]; then
            sed -i "s/^wpa2_passphrase = .*/wpa2_passphrase = \"${WIFIPASS//\//\\/}\"/" "$CONFIG_DIR/config.toml"
        fi
    else
        warn "Non-interactive install: edit $CONFIG_DIR/config.toml afterwards (SSID, admin PIN, Spotify keys)."
    fi
else
    say "Keeping existing $CONFIG_DIR/config.toml"
fi

# This file holds the Spotify client secret and both PINs in plain text —
# readable by the clinicjam group only, not by other local accounts. The
# directory itself needs the same group so clinicjam-web/librespot/
# portal-watch (which run as that user) can still traverse into it.
chgrp clinicjam "$CONFIG_DIR" "$CONFIG_DIR/config.toml" 2>/dev/null || true
chmod 750 "$CONFIG_DIR"
chmod 640 "$CONFIG_DIR/config.toml"

# ------------------------------------------------------------------ uplink
# How the Pi itself reaches the internet (Spotify, speaker check-in) —
# separate from the guest jam Wi-Fi. Defaults to a no-op (cellular/ethernet,
# handled natively by NetworkManager). See [uplink] in config.toml for the
# wifi_client option (joining an existing network like the company's guest
# Wi-Fi through a second radio) and docs/NETWORK.md for captive-portal
# auto-accept setup.
say "Configuring the Pi's own internet uplink..."
chmod +x "$APP_DIR/install/setup-uplink.sh"
"$APP_DIR/install/setup-uplink.sh" || warn "uplink setup needs attention (see messages above) — fix $CONFIG_DIR/config.toml and re-run: sudo $APP_DIR/install/setup-uplink.sh"

# ------------------------------------------------------------------ python env
say "Setting up the Python environment..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/server/requirements.txt"

# ------------------------------------------------------------------ helpers
install -m 755 "$APP_DIR/install/setup-ap-interface.sh" "$APP_DIR/bin/setup-ap-interface.sh"
install -m 755 "$APP_DIR/install/render_network.py" "$APP_DIR/bin/render_network.py"
cat > /usr/local/sbin/clinicjam-apply <<EOF
#!/bin/sh
# Re-render network configs from config.toml and reload services.
set -e
python3 $APP_DIR/bin/render_network.py
systemctl restart clinicjam-firewall clinicjam-hostapd clinicjam-dnsmasq
echo "clinicjam: network config applied"
EOF
chmod 755 /usr/local/sbin/clinicjam-apply

# librespot env (device name comes from config.toml)
DEVICE_NAME=$(python3 -c "
import tomllib
cfg = tomllib.load(open('$CONFIG_DIR/config.toml','rb'))
print(cfg.get('spotify',{}).get('device_name','ClinicJam'))")
echo "DEVICE_NAME=$DEVICE_NAME" > "$CONFIG_DIR/librespot.env"

# ------------------------------------------------------------------ network configs
say "Rendering hostapd/dnsmasq/nftables from config.toml..."
python3 "$APP_DIR/bin/render_network.py" "$CONFIG_DIR/config.toml"

# Keep NetworkManager's hands off the AP interface (it may manage the
# cellular modem / ethernet uplink, which is exactly what we want).
AP_IF=$(python3 -c "
import tomllib
cfg = tomllib.load(open('$CONFIG_DIR/config.toml','rb'))
print(cfg.get('network',{}).get('ap_interface','wlan0'))")
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/clinicjam-unmanaged.conf <<EOF
[keyfile]
unmanaged-devices=interface-name:$AP_IF
EOF
systemctl reload NetworkManager 2>/dev/null || true

# Routing on (the firewall decides who may actually be forwarded).
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/90-clinicjam.conf
sysctl -q --system

# Cap how much of the SD card the system log can eat over months of
# unattended uptime — journald's default is sized for a desktop with much
# more disk, and runs several always-on services here.
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/clinicjam.conf <<'EOF'
[Journal]
SystemMaxUse=200M
SystemMaxFileSize=20M
EOF
systemctl restart systemd-journald 2>/dev/null || true

# ------------------------------------------------------------------ services
say "Installing systemd services..."
install -m 644 "$APP_DIR"/install/systemd/clinicjam-*.service /etc/systemd/system/
install -m 644 "$APP_DIR/install/systemd/clinicjam-tmpfiles.conf" /etc/tmpfiles.d/clinicjam.conf
systemd-tmpfiles --create /etc/tmpfiles.d/clinicjam.conf
systemctl daemon-reload
systemctl enable --now \
    clinicjam-network clinicjam-firewall clinicjam-hostapd \
    clinicjam-dnsmasq clinicjam-web clinicjam-librespot clinicjam-portal-watch

AP_IP=$(python3 -c "
import tomllib
cfg = tomllib.load(open('$CONFIG_DIR/config.toml','rb'))
print(cfg.get('network',{}).get('ap_ip','10.24.7.1'))")
UPLINK_MODE=$(python3 -c "
import tomllib
cfg = tomllib.load(open('$CONFIG_DIR/config.toml','rb'))
print(cfg.get('uplink',{}).get('mode','cellular'))")

say "ClinicJam is installed! 🎵"
cat <<EOF

  Next steps:
    1. Add your Spotify API keys to $CONFIG_DIR/config.toml
       (docs/SPOTIFY_SETUP.md), then: sudo systemctl restart clinicjam-web
    2. Join the '$DEVICE_NAME' guest experience:
         Wi-Fi network:  (the SSID you chose)
         Guest app:      http://$AP_IP/
         Host panel:     http://$AP_IP/admin
    3. Put your 5 Nest speakers on the jam Wi-Fi and allowlist their MACs
       (docs/NEST_SETUP.md), then: sudo clinicjam-apply
EOF

if [ "$UPLINK_MODE" = "wifi_client" ]; then
cat <<EOF
    4. Uplink is set to 'wifi_client' — the Pi joins another Wi-Fi network
       (e.g. the company's guest network) through a second radio for its
       own internet. If that network has a captive portal, set it up to
       auto-accept (docs/NETWORK.md#automating-a-captive-portal-uplink).
       If it's just a single "I agree" button, this usually works as-is:
         cp $APP_DIR/docs/portal_click.example.py $CONFIG_DIR/portal_click.py
         sudo systemctl restart clinicjam-portal-watch
       Otherwise edit $CONFIG_DIR/portal_click.py per the docs.
EOF
fi

cat <<EOF

  Service health:  systemctl status 'clinicjam-*'
  Logs:            journalctl -u clinicjam-web -f
EOF
