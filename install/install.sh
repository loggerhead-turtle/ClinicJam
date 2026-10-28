#!/bin/bash
# ClinicJam installer for Raspberry Pi OS (Trixie/Bookworm, Pi 4/5, 64-bit).
#
# Normally you don't run this: flash the ready-made image instead
# (docs/QUICKSTART.md). It is here for installing onto an existing Pi OS:
#
#   curl -fsSL https://raw.githubusercontent.com/loggerhead-turtle/ClinicJam/main/install/install.sh | sudo bash
#   sudo ./install/install.sh            # from a clone
#
# It is also what the image build runs inside the chroot
# (CLINICJAM_IMAGE_BUILD=1), so the image and a manual install are identical.
#
# Safe to re-run: it upgrades in place and keeps /etc/clinicjam/config.toml.

set -euo pipefail

REPO_URL="${CLINICJAM_REPO_URL:-https://github.com/loggerhead-turtle/ClinicJam.git}"
REPO_REF="${CLINICJAM_REPO_REF:-main}"
APP_DIR=/opt/clinicjam
CONFIG_DIR=/etc/clinicjam
DATA_DIR=/var/lib/clinicjam
IMAGE_BUILD="${CLINICJAM_IMAGE_BUILD:-0}"

say()  { echo -e "\e[1;32m==>\e[0m $*"; }
warn() { echo -e "\e[1;33m!!\e[0m $*"; }
die()  { echo -e "\e[1;31mxx\e[0m $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "Run with sudo: sudo bash install.sh"
command -v apt-get >/dev/null || die "This installer targets Raspberry Pi OS (Debian)."

# ------------------------------------------------------------------ packages
say "Installing packages (this can take a few minutes)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    hostapd dnsmasq nftables conntrack ffmpeg curl git rfkill iw bridge-utils \
    python3 python3-venv python3-pip ca-certificates gpg > /dev/null

# Stock hostapd/dnsmasq services would fight with our dedicated units.
systemctl disable hostapd dnsmasq 2>/dev/null || true
[ "$IMAGE_BUILD" = 1 ] || systemctl stop hostapd dnsmasq 2>/dev/null || true
systemctl unmask hostapd 2>/dev/null || true

# librespot: the raspotify project publishes a signed apt repo with a
# well-built arm64 binary. We add the repo + key ourselves (apt verifies
# the signature) rather than piping their install script into sh.
if ! command -v librespot >/dev/null; then
    say "Installing librespot (raspotify apt repository)..."
    curl -fsSL https://dtcooper.github.io/raspotify/key.asc -o /usr/share/keyrings/raspotify_key.asc
    chmod 644 /usr/share/keyrings/raspotify_key.asc
    echo 'deb [signed-by=/usr/share/keyrings/raspotify_key.asc] https://dtcooper.github.io/raspotify raspotify main' \
        > /etc/apt/sources.list.d/raspotify.list
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends raspotify > /dev/null
fi
# We run librespot ourselves; raspotify's own service must not grab it.
systemctl disable raspotify 2>/dev/null || true
[ "$IMAGE_BUILD" = 1 ] || systemctl stop raspotify 2>/dev/null || true
command -v librespot >/dev/null || die "librespot did not install — see https://github.com/dtcooper/raspotify"

# ------------------------------------------------------------------ code
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-/dev/null}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/../server/requirements.txt" ]; then
    SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
    if [ "$SRC_DIR" != "$APP_DIR" ]; then
        say "Installing ClinicJam from $SRC_DIR into $APP_DIR..."
        mkdir -p "$APP_DIR"
        # Copy the working tree (not .git, not venvs).
        tar -C "$SRC_DIR" --exclude=.git --exclude=venv --exclude=.venv \
            --exclude='__pycache__' --exclude=.pytest_cache -cf - . | tar -C "$APP_DIR" -xf -
    fi
elif [ ! -d "$APP_DIR/.git" ]; then
    say "Fetching ClinicJam ($REPO_REF) into $APP_DIR..."
    git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$APP_DIR"
else
    say "Keeping existing code in $APP_DIR (run 'git -C $APP_DIR pull' to update)"
fi

# ------------------------------------------------------------------ user + dirs
id clinicjam >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin clinicjam
mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$APP_DIR/bin"
chown -R clinicjam:clinicjam "$DATA_DIR"
chmod 750 "$DATA_DIR"
chown root:clinicjam "$CONFIG_DIR"
chmod 750 "$CONFIG_DIR"

# ------------------------------------------------------------------ config
if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    say "Creating $CONFIG_DIR/config.toml..."
    install -m 640 -o root -g clinicjam "$APP_DIR/config.example.toml" "$CONFIG_DIR/config.toml"
    if [ "$IMAGE_BUILD" != 1 ] && [ -t 0 ]; then
        read -rp "Wi-Fi network name for guests [ClinicJam]: " SSID
        read -rp "Wi-Fi password for that network (8+ chars, empty = open): " WPA
        read -rp "Admin PIN for the host panel (6+ digits): " PIN
        read -rp "Jam PIN guests enter to join (empty = no PIN) [1357]: " GUESTPIN
        read -rp "Two-letter Wi-Fi country code [US]: " COUNTRY
        python3 "$APP_DIR/install/apply_settings.py" --set \
            "jam_wifi_name=${SSID:-ClinicJam}" "jam_wifi_password=${WPA:-}" \
            "admin_pin=${PIN:-}" "guest_pin=${GUESTPIN-1357}" "country=${COUNTRY:-US}" \
            || warn "could not apply the answers; edit $CONFIG_DIR/config.toml by hand"
    else
        warn "No prompts: edit $CONFIG_DIR/config.toml (or clinicjam-settings.txt on the boot partition) and run 'sudo clinicjam-apply'."
    fi
else
    say "Keeping existing $CONFIG_DIR/config.toml"
    chown root:clinicjam "$CONFIG_DIR/config.toml"; chmod 640 "$CONFIG_DIR/config.toml"
fi

# ------------------------------------------------------------------ python env
say "Setting up the Python environment..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/server/requirements.txt"

# ------------------------------------------------------------------ helpers
install -m 755 "$APP_DIR/install/setup-ap-interface.sh" "$APP_DIR/bin/setup-ap-interface.sh"
install -m 755 "$APP_DIR/install/ap-hairpin.sh" "$APP_DIR/bin/ap-hairpin.sh"
install -m 755 "$APP_DIR/install/run-librespot.sh" "$APP_DIR/bin/run-librespot.sh"
install -m 755 "$APP_DIR/install/render_network.py" "$APP_DIR/bin/render_network.py"
install -m 755 "$APP_DIR/install/apply_settings.py" "$APP_DIR/bin/apply_settings.py"
install -m 755 "$APP_DIR/install/clinicjam-fw" /usr/local/sbin/clinicjam-fw
cat > /usr/local/sbin/clinicjam-apply <<EOF
#!/bin/sh
# Re-render network configs from config.toml and reload services.
set -e
python3 $APP_DIR/bin/render_network.py
if [ -d /run/systemd/system ]; then
    systemctl restart clinicjam-network clinicjam-firewall clinicjam-hostapd \\
        clinicjam-dnsmasq clinicjam-dns-nest clinicjam-web clinicjam-librespot
fi
echo "clinicjam: network config applied"
EOF
chmod 755 /usr/local/sbin/clinicjam-apply

# librespot env (device name comes from config.toml)
DEVICE_NAME=$(python3 -c "
import tomllib
cfg = tomllib.load(open('$CONFIG_DIR/config.toml','rb'))
print(cfg.get('spotify',{}).get('device_name','ClinicJam'))")
cat > "$CONFIG_DIR/librespot.env" <<EOF
DEVICE_NAME=$DEVICE_NAME
CACHE_DIR=$DATA_DIR/librespot-cache
TOKEN_FILE=$DATA_DIR/librespot-token
FIFO=/run/clinicjam/audio.fifo
EOF
chmod 644 "$CONFIG_DIR/librespot.env"
[ -f "$CONFIG_DIR/allowlist.json" ] || { echo '{}' > "$CONFIG_DIR/allowlist.json"; chmod 644 "$CONFIG_DIR/allowlist.json"; }

# ------------------------------------------------------------------ network configs
say "Rendering hostapd/dnsmasq/nftables from config.toml..."
python3 "$APP_DIR/bin/render_network.py" "$CONFIG_DIR/config.toml"

# Keep NetworkManager's hands off the jam interfaces (it manages the
# uplink — cellular modem / ethernet / the Wi-Fi dongle — which is what we want).
AP_IF=$(python3 -c "
import tomllib
cfg = tomllib.load(open('$CONFIG_DIR/config.toml','rb'))
print(cfg.get('network',{}).get('ap_interface','wlan0'))")
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/clinicjam-unmanaged.conf <<EOF
[keyfile]
unmanaged-devices=interface-name:$AP_IF;interface-name:br0
EOF
[ "$IMAGE_BUILD" = 1 ] || systemctl reload NetworkManager 2>/dev/null || true

# Routing on (the firewall decides who may actually be forwarded).
cat > /etc/sysctl.d/90-clinicjam.conf <<'EOF'
net.ipv4.ip_forward=1
# Guests can't reach the Pi over IPv6 either way; keep it simple.
net.ipv6.conf.all.forwarding=0
EOF
[ "$IMAGE_BUILD" = 1 ] || sysctl -q --system

# ------------------------------------------------------------------ services
say "Installing systemd services..."
install -m 644 "$APP_DIR"/install/systemd/clinicjam-*.service "$APP_DIR"/install/systemd/clinicjam-*.path /etc/systemd/system/
install -m 644 "$APP_DIR/install/systemd/clinicjam-tmpfiles.conf" /etc/tmpfiles.d/clinicjam.conf
install -m 644 "$APP_DIR/install/systemd/clinicjam-firstboot.service" /etc/systemd/system/clinicjam-firstboot.service
UNITS="clinicjam-network clinicjam-firewall clinicjam-hostapd clinicjam-dnsmasq clinicjam-dns-nest \
       clinicjam-web clinicjam-librespot clinicjam-portal-watch clinicjam-fw.path clinicjam-firstboot"
if [ "$IMAGE_BUILD" = 1 ]; then
    # shellcheck disable=SC2086
    systemctl enable $UNITS >/dev/null
else
    systemd-tmpfiles --create /etc/tmpfiles.d/clinicjam.conf
    systemctl daemon-reload
    # shellcheck disable=SC2086
    systemctl enable $UNITS >/dev/null
    # Uplink (the Pi's own internet) per [uplink] in config.toml.
    say "Configuring the Pi's own internet uplink..."
    chmod +x "$APP_DIR/install/setup-uplink.sh"
    "$APP_DIR/install/setup-uplink.sh" || warn "uplink setup needs attention (see above) — fix $CONFIG_DIR/config.toml and re-run: sudo $APP_DIR/install/setup-uplink.sh"
    # shellcheck disable=SC2086
    systemctl restart $UNITS
fi

AP_IP=$(python3 -c "
import tomllib
cfg = tomllib.load(open('$CONFIG_DIR/config.toml','rb'))
print(cfg.get('network',{}).get('ap_ip','10.24.7.1'))")

say "ClinicJam is installed! 🎵"
cat <<EOF

  Next steps (docs/QUICKSTART.md has pictures of each):
    1. Join the jam Wi-Fi with your phone and open  http://$AP_IP/admin
    2. Log in with your admin PIN and tap "Open Spotify login" — one paste
       links both the account and the player. No developer app needed.
    3. Tap "Turn on setup mode", put the Nest speakers on the jam Wi-Fi with
       the Google Home app, create a speaker group, pick it under Speakers.
    4. Press "Start the jam".

  Service health:  systemctl status 'clinicjam-*'
  Logs:            journalctl -u clinicjam-web -f
EOF
