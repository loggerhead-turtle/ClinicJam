#!/bin/bash
# ClinicJam installer for Raspberry Pi OS Bookworm (Pi 4/5, 64-bit).
#
# One-line install:
#   curl -fsSL https://raw.githubusercontent.com/loggerhead-turtle/ClinicJam/main/install/install.sh | sudo bash
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

# ------------------------------------------------------------------ config
if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    say "Creating $CONFIG_DIR/config.toml..."
    cp "$APP_DIR/config.example.toml" "$CONFIG_DIR/config.toml"

    if [ -t 0 ]; then
        read -rp "Wi-Fi network name for guests [ClinicJam]: " SSID
        read -rp "Admin PIN for the host panel [2468]: " PIN
        read -rp "Jam PIN guests enter to join (empty = no PIN) [1357]: " GUESTPIN
        read -rp "Two-letter Wi-Fi country code [US]: " COUNTRY
        SSID=${SSID:-ClinicJam}; PIN=${PIN:-2468}; GUESTPIN=${GUESTPIN-1357}; COUNTRY=${COUNTRY:-US}
        sed -i "s/^ssid = .*/ssid = \"${SSID//\//\\/}\"/" "$CONFIG_DIR/config.toml"
        sed -i "s/^admin_pin = .*/admin_pin = \"${PIN//\//\\/}\"/" "$CONFIG_DIR/config.toml"
        sed -i "s/^guest_pin = .*/guest_pin = \"${GUESTPIN//\//\\/}\"/" "$CONFIG_DIR/config.toml"
        sed -i "s/^country = .*/country = \"${COUNTRY//\//\\/}\"/" "$CONFIG_DIR/config.toml"
    else
        warn "Non-interactive install: edit $CONFIG_DIR/config.toml afterwards (SSID, admin PIN, Spotify keys)."
    fi
else
    say "Keeping existing $CONFIG_DIR/config.toml"
fi

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

# ------------------------------------------------------------------ services
say "Installing systemd services..."
install -m 644 "$APP_DIR"/install/systemd/clinicjam-*.service /etc/systemd/system/
install -m 644 "$APP_DIR/install/systemd/clinicjam-tmpfiles.conf" /etc/tmpfiles.d/clinicjam.conf
systemd-tmpfiles --create /etc/tmpfiles.d/clinicjam.conf
systemctl daemon-reload
systemctl enable --now \
    clinicjam-network clinicjam-firewall clinicjam-hostapd \
    clinicjam-dnsmasq clinicjam-web clinicjam-librespot

AP_IP=$(python3 -c "
import tomllib
cfg = tomllib.load(open('$CONFIG_DIR/config.toml','rb'))
print(cfg.get('network',{}).get('ap_ip','10.24.7.1'))")

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

  Service health:  systemctl status 'clinicjam-*'
  Logs:            journalctl -u clinicjam-web -f
EOF
