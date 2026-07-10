#!/bin/sh
# Bring up the jam AP interface with its static address.
# Values are read from config.toml so there is one source of truth.
set -eu

CONFIG=/etc/clinicjam/config.toml

get() {
    python3 - "$1" <<'EOF'
import sys, tomllib
with open("/etc/clinicjam/config.toml", "rb") as f:
    cfg = tomllib.load(f)
section, key = sys.argv[1].split(".")
print(cfg.get(section, {}).get(key, ""))
EOF
}

AP_IF="$(get network.ap_interface)"
AP_IP="$(get network.ap_ip)"

rfkill unblock wlan || true
ip link set "$AP_IF" up
ip addr replace "$AP_IP/24" dev "$AP_IF"
echo "clinicjam: $AP_IF up at $AP_IP/24"
