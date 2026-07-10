#!/bin/bash
# Optional add-on: boot the Pi's touchscreen straight into the ClinicJam
# kiosk UI. Uses cage (a minimal Wayland kiosk compositor) + Chromium in
# kiosk mode — no desktop environment needed, works on Pi OS Lite too.
#
#   sudo /opt/clinicjam/install/setup-kiosk.sh
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }

echo "==> Installing kiosk packages (cage + chromium)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq cage seatd > /dev/null
# Pi OS ships the browser as chromium-browser; other Debians as chromium.
apt-get install -y -qq chromium-browser > /dev/null 2>&1 \
    || apt-get install -y -qq chromium > /dev/null

BROWSER=$(command -v chromium-browser || command -v chromium)
[ -n "$BROWSER" ] || { echo "chromium not found after install"; exit 1; }

echo "==> Creating kiosk user..."
id kiosk >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin kiosk
usermod -aG video,render,input kiosk
systemctl enable --now seatd 2>/dev/null || true
usermod -aG _seatd kiosk 2>/dev/null || usermod -aG seat kiosk 2>/dev/null || true

PORT=$(python3 -c "
import tomllib
cfg = tomllib.load(open('/etc/clinicjam/config.toml','rb'))
print(cfg.get('server',{}).get('port',8080))" 2>/dev/null || echo 8080)

echo "==> Writing kiosk launcher..."
mkdir -p /opt/clinicjam/bin
cat > /opt/clinicjam/bin/start-kiosk.sh <<EOF
#!/bin/sh
# Wait for the jam server before launching the browser, so a cold boot
# never shows a Chromium error page.
until curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/state"; do
    sleep 2
done
exec cage -- $BROWSER \\
    --kiosk \\
    --noerrdialogs \\
    --disable-infobars \\
    --no-first-run \\
    --disable-session-crashed-bubble \\
    --disable-pinch \\
    --overscroll-history-navigation=0 \\
    --ozone-platform=wayland \\
    --user-data-dir=/home/kiosk/.clinicjam-kiosk \\
    "http://127.0.0.1:$PORT/kiosk"
EOF
chmod 755 /opt/clinicjam/bin/start-kiosk.sh

echo "==> Installing clinicjam-kiosk.service..."
cat > /etc/systemd/system/clinicjam-kiosk.service <<'EOF'
[Unit]
Description=ClinicJam touchscreen kiosk (cage + chromium)
After=clinicjam-web.service systemd-user-sessions.service
Wants=clinicjam-web.service
Conflicts=getty@tty1.service

[Service]
User=kiosk
PAMName=login
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal
TTYReset=yes
TTYVHangup=yes
UtmpIdentifier=tty1
Environment=XDG_RUNTIME_DIR=/run/user/%U
ExecStart=/opt/clinicjam/bin/start-kiosk.sh
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF

# Don't fight over tty1 with the console login.
systemctl disable getty@tty1.service 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now clinicjam-kiosk

cat <<'EOF'

Kiosk installed! The touchscreen boots straight into the jam.

  Touchscreen tips:
    - Official 7" display: rotate if needed with 'display_auto_detect=1'
      plus 'lcd_rotate=2' in /boot/firmware/config.txt
    - The kiosk page has its own on-screen keyboard; no OS keyboard needed.
    - Host controls live behind the gear icon (your admin PIN).

  Manage it:
    systemctl status clinicjam-kiosk
    sudo systemctl restart clinicjam-kiosk
    sudo systemctl disable --now clinicjam-kiosk   # turn the kiosk off
EOF
