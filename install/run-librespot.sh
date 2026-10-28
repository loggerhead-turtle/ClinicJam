#!/bin/sh
# Launcher for the Spotify Connect player (librespot).
#
# librespot can only sign in with an OAuth token (password login is
# gone). The admin panel completes the login once and drops the token in
# $TOKEN_FILE; librespot then stores *reusable* credentials in its cache
# and never needs the token again. Until a login exists we simply wait —
# starting librespot with no credentials and discovery disabled would
# just exit in a loop.
#
# Discovery (zeroconf / "Spotify Connect" on the LAN) is deliberately OFF:
# with it on, anyone on the Wi-Fi could log *their* account into the
# office speakers.
set -u
. /etc/clinicjam/librespot.env

CACHE_DIR=${CACHE_DIR:-/var/lib/clinicjam/librespot-cache}
TOKEN_FILE=${TOKEN_FILE:-/var/lib/clinicjam/librespot-token}
FIFO=${FIFO:-/run/clinicjam/audio.fifo}
mkdir -p "$CACHE_DIR"
chmod 700 "$CACHE_DIR"

while [ ! -s "$TOKEN_FILE" ] && [ ! -s "$CACHE_DIR/credentials.json" ]; do
    echo "clinicjam-librespot: waiting for the Spotify player to be linked (admin panel -> Connect Spotify)"
    sleep 15
done

set -- /usr/bin/librespot \
    --name "${DEVICE_NAME:-ClinicJam}" \
    --backend pipe \
    --device "$FIFO" \
    --bitrate 320 \
    --format S16 \
    --onevent /opt/clinicjam/bin/onevent.sh \
    --cache "$CACHE_DIR" \
    --cache-size-limit 1G \
    --initial-volume 100 \
    --volume-ctrl fixed \
    --disable-discovery \
    --quiet

if [ -s "$TOKEN_FILE" ]; then
    # A fresh login: prefer the new token over any cached credentials
    # (the host may be switching accounts). The token is single-use.
    TOKEN=$(cat "$TOKEN_FILE")
    rm -f "$TOKEN_FILE" "$CACHE_DIR/credentials.json"
    echo "clinicjam-librespot: signing in with a fresh token"
    exec "$@" --access-token "$TOKEN"
fi

echo "clinicjam-librespot: signing in with cached credentials"
exec "$@"
