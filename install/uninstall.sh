#!/bin/bash
# Remove ClinicJam services and network config. Keeps /etc/clinicjam and
# /var/lib/clinicjam unless --purge is given.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }

systemctl disable --now \
    clinicjam-web clinicjam-librespot clinicjam-hostapd \
    clinicjam-dnsmasq clinicjam-dns-nest clinicjam-firewall clinicjam-network \
    clinicjam-portal-watch clinicjam-kiosk clinicjam-fw.path clinicjam-firstboot 2>/dev/null || true
rm -f /etc/systemd/system/clinicjam-*.service /etc/systemd/system/clinicjam-*.path /etc/tmpfiles.d/clinicjam.conf
systemctl daemon-reload

nft delete table inet clinicjam 2>/dev/null || true
nft delete table ip clinicjam_nat 2>/dev/null || true
nft delete table bridge clinicjam 2>/dev/null || true
ip link del br0 2>/dev/null || true
rm -f /etc/NetworkManager/conf.d/clinicjam-unmanaged.conf \
      /etc/sysctl.d/90-clinicjam.conf /usr/local/sbin/clinicjam-apply /usr/local/sbin/clinicjam-fw
nmcli connection delete clinicjam-uplink 2>/dev/null || true
systemctl reload NetworkManager 2>/dev/null || true

if [ "${1:-}" = "--purge" ]; then
    rm -rf /opt/clinicjam /etc/clinicjam /var/lib/clinicjam
    userdel clinicjam 2>/dev/null || true
    echo "ClinicJam fully removed."
else
    echo "Services removed. Config kept in /etc/clinicjam (use --purge to delete everything)."
fi
