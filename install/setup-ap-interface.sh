#!/bin/sh
# Bring up the jam network interface with its static addresses.
# With client isolation (the default) the radio is enslaved to a bridge
# (br0) by hostapd; the bridge carries the addresses and the firewall's
# "bridge" rules decide which stations may talk to each other.
set -eu

ENV=/etc/clinicjam/network.env
[ -f "$ENV" ] || { echo "clinicjam: $ENV missing — run clinicjam-apply"; exit 1; }
# shellcheck disable=SC1090
. "$ENV"

rfkill unblock wlan || true
ip link set "$AP_IF" up || true

if [ "$CLIENT_ISOLATION" = "1" ]; then
    ip link add name br0 type bridge 2>/dev/null || true
    # No STP, forward immediately, don't learn from flooded frames.
    ip link set br0 type bridge stp_state 0 forward_delay 0 2>/dev/null || true
    ip link set br0 up
fi

ip addr replace "$AP_IP/24" dev "$JAM_IF"
ip addr replace "$DNS_IP/24" dev "$JAM_IF"
echo "clinicjam: $JAM_IF up at $AP_IP/24 (+$DNS_IP for the speakers' resolver)"
