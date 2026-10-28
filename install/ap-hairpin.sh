#!/bin/sh
# Runs after hostapd starts. hostapd adds the radio to br0 itself; we then
# turn on hairpin mode so station-to-station frames (which ap_isolate
# forces up to the host) are re-sent out of the radio — subject to the
# nftables bridge rules (speakers <-> speakers only, or everyone during
# setup mode).
set -u
ENV=/etc/clinicjam/network.env
# shellcheck disable=SC1090
[ -f "$ENV" ] && . "$ENV"
[ "${CLIENT_ISOLATION:-0}" = "1" ] || exit 0

i=0
while [ $i -lt 30 ]; do
    if bridge link show dev "$AP_IF" 2>/dev/null | grep -q "master br0"; then
        bridge link set dev "$AP_IF" hairpin on && echo "clinicjam: hairpin on for $AP_IF"
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done
echo "clinicjam: $AP_IF never joined br0 — is hostapd running?" >&2
exit 0
