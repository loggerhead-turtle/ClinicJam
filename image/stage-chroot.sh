#!/bin/bash
# Runs INSIDE the Raspberry Pi OS chroot (arm64, via qemu-user-static).
# Turns a stock Raspberry Pi OS Lite into the ClinicJam appliance using
# the very same installer a manual install uses.
set -euo pipefail

export CLINICJAM_IMAGE_BUILD=1
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C.UTF-8

SRC=/tmp/clinicjam-src

echo "==> [chroot] base system"
# Never start services while we build (policy-rc.d is installed by the host).
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates curl gpg > /dev/null

echo "==> [chroot] ClinicJam installer"
bash "$SRC/install/install.sh"

echo "==> [chroot] appliance polish"
echo clinicjam > /etc/hostname
sed -i 's/^127\.0\.1\.1.*/127.0.1.1\tclinicjam/' /etc/hosts
grep -q '^127\.0\.1\.1' /etc/hosts || echo -e '127.0.1.1\tclinicjam' >> /etc/hosts

# Things an appliance doesn't need running (RAM + boot time on a 2 GB Pi).
systemctl disable bluetooth.service hciuart.service 2>/dev/null || true
systemctl disable apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl disable ModemManager.service 2>/dev/null || true  # re-enable for a USB cellular modem
# SSH is turned on by clinicjam-settings.txt (login_password) — off until then.
systemctl disable ssh.service 2>/dev/null || true

# Keep the SD card happy: journald in RAM (Pi OS default), small swap.
sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=256/' /etc/dphys-swapfile 2>/dev/null || true

# A friendly console banner.
cat > /etc/motd <<'EOF'

  ClinicJam appliance
  -------------------
  Guest app:   http://10.24.7.1/        (on the jam Wi-Fi)
  Host panel:  http://10.24.7.1/admin
  Settings:    clinicjam-settings.txt on the SD card's boot partition
  Health:      systemctl status 'clinicjam-*'      Logs: journalctl -u clinicjam-web -f

EOF

echo "==> [chroot] cleanup"
/opt/clinicjam/venv/bin/pip cache purge >/dev/null 2>&1 || true
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/clinicjam-src /root/.cache /var/cache/apt/*.bin
find /opt/clinicjam -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
echo "==> [chroot] done"
