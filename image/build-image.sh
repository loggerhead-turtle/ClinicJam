#!/bin/bash
# Build the flashable ClinicJam image: stock Raspberry Pi OS Lite (64-bit)
# + everything installed and enabled, so the Pi only needs the settings
# file on the boot partition.
#
#   sudo image/build-image.sh            # -> out/clinicjam-<version>.img.xz
#
# Needs a Debian/Ubuntu host (GitHub Actions ubuntu-24.04 works) with:
#   qemu-user-static binfmt-support xz-utils parted e2fsprogs dosfstools
set -euo pipefail

BASE_URL="${BASE_URL:-https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/2026-10-06-raspios-trixie-arm64-lite.img.xz}"
BASE_SHA256="${BASE_SHA256:-483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843}"
GROW_MB="${GROW_MB:-2560}"
DEFAULT_LOGIN_USER="admin"
DEFAULT_LOGIN_PASSWORD="clinicjam"   # only works for the console until login_password is set

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
WORK="${WORK:-$SRC/image/build}"
OUT="${OUT:-$SRC/image/out}"
VERSION="${VERSION:-$(date -u +%Y.%m.%d)-$(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo local)}"
IMG="$WORK/clinicjam-$VERSION.img"
MNT="$WORK/mnt"

say() { echo -e "\e[1;34m==>\e[0m $*"; }
die() { echo -e "\e[1;31mxx\e[0m $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root (sudo)"
for tool in xz parted losetup e2fsck resize2fs qemu-aarch64-static openssl sha256sum; do
    command -v "$tool" >/dev/null || die "missing tool: $tool"
done
mkdir -p "$WORK" "$OUT"

LOOP=""
cleanup() {
    set +e
    for d in "$MNT/dev/pts" "$MNT/dev" "$MNT/proc" "$MNT/sys" "$MNT/boot/firmware" "$MNT"; do
        mountpoint -q "$d" 2>/dev/null && umount -l "$d"
    done
    [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null
}
trap cleanup EXIT

# ------------------------------------------------------------------ base image
BASE_XZ="$WORK/$(basename "$BASE_URL")"
if [ ! -f "$BASE_XZ" ]; then
    say "Downloading Raspberry Pi OS Lite..."
    curl -fL --retry 5 -o "$BASE_XZ.part" "$BASE_URL" && mv "$BASE_XZ.part" "$BASE_XZ"
fi
say "Verifying checksum..."
echo "$BASE_SHA256  $BASE_XZ" | sha256sum -c - >/dev/null || die "checksum mismatch for $BASE_XZ"

say "Decompressing to $IMG..."
rm -f "$IMG"
xz -dkc -T0 "$BASE_XZ" > "$IMG"

# ------------------------------------------------------------------ grow rootfs
say "Growing the root partition by ${GROW_MB} MB..."
truncate -s "+${GROW_MB}M" "$IMG"
parted -s "$IMG" resizepart 2 100%
LOOP=$(losetup --show -fP "$IMG")
sleep 1
e2fsck -pf "${LOOP}p2" >/dev/null || true
resize2fs "${LOOP}p2" >/dev/null

# ------------------------------------------------------------------ mount + chroot prep
say "Mounting..."
mkdir -p "$MNT"
mount "${LOOP}p2" "$MNT"
mkdir -p "$MNT/boot/firmware"
mount "${LOOP}p1" "$MNT/boot/firmware"
mount --bind /dev "$MNT/dev"
mount --bind /dev/pts "$MNT/dev/pts"
mount -t proc proc "$MNT/proc"
mount -t sysfs sys "$MNT/sys"
cp "$(command -v qemu-aarch64-static)" "$MNT/usr/bin/"
cp -L /etc/resolv.conf "$MNT/etc/resolv.conf.build"
mv "$MNT/etc/resolv.conf" "$MNT/etc/resolv.conf.orig" 2>/dev/null || true
cp "$MNT/etc/resolv.conf.build" "$MNT/etc/resolv.conf"
# Don't let package installs start daemons inside the chroot.
printf '#!/bin/sh\nexit 101\n' > "$MNT/usr/sbin/policy-rc.d"
chmod 755 "$MNT/usr/sbin/policy-rc.d"
# The Pi OS ld.so.preload trick (libarmmem) breaks under qemu.
[ -f "$MNT/etc/ld.so.preload" ] && mv "$MNT/etc/ld.so.preload" "$MNT/etc/ld.so.preload.build" || true

say "Copying sources..."
rm -rf "$MNT/tmp/clinicjam-src"
mkdir -p "$MNT/tmp/clinicjam-src"
tar -C "$SRC" --exclude=.git --exclude=image/build --exclude=image/out --exclude=venv \
    --exclude='*/venv' --exclude='__pycache__' --exclude=.pytest_cache -cf - . \
    | tar -C "$MNT/tmp/clinicjam-src" -xf -

# ------------------------------------------------------------------ chroot install
say "Running the installer inside the image (arm64 via qemu — slow, be patient)..."
chroot "$MNT" /bin/bash /tmp/clinicjam-src/image/stage-chroot.sh

# ------------------------------------------------------------------ boot partition
say "Preparing the boot partition..."
BOOT="$MNT/boot/firmware"
cp "$HERE/clinicjam-settings.txt" "$BOOT/clinicjam-settings.txt"
cp "$SRC/docs/QUICKSTART.md" "$BOOT/README-CLINICJAM.txt" 2>/dev/null || true
# Pi OS creates the login user on first boot from this file.
HASH=$(openssl passwd -6 "$DEFAULT_LOGIN_PASSWORD")
echo "$DEFAULT_LOGIN_USER:$HASH" > "$BOOT/userconf.txt"
# Make sure a Wi-Fi regulatory domain exists so the radio isn't blocked.
if ! grep -q 'cfg80211.ieee80211_regdom' "$BOOT/cmdline.txt"; then
    sed -i '1 s/$/ cfg80211.ieee80211_regdom=US/' "$BOOT/cmdline.txt"
fi
# The 7" touch display and most HDMI screens work out of the box; no changes.

# ------------------------------------------------------------------ cleanup
say "Cleaning up the chroot..."
rm -f "$MNT/usr/sbin/policy-rc.d" "$MNT/usr/bin/qemu-aarch64-static" "$MNT/etc/resolv.conf.build"
[ -f "$MNT/etc/ld.so.preload.build" ] && mv "$MNT/etc/ld.so.preload.build" "$MNT/etc/ld.so.preload" || true
rm -f "$MNT/etc/resolv.conf"
[ -e "$MNT/etc/resolv.conf.orig" ] && mv "$MNT/etc/resolv.conf.orig" "$MNT/etc/resolv.conf" || true
rm -rf "$MNT/tmp/clinicjam-src"
echo "ClinicJam $VERSION (built $(date -u +%FT%TZ))" > "$MNT/etc/clinicjam-release"

# Zero free space so the .xz is small.
say "Zeroing free space (makes the download small)..."
dd if=/dev/zero of="$MNT/zero.fill" bs=4M status=none 2>/dev/null || true
rm -f "$MNT/zero.fill"
sync

umount "$MNT/dev/pts" "$MNT/dev" "$MNT/proc" "$MNT/sys"
umount "$MNT/boot/firmware"
umount "$MNT"
losetup -d "$LOOP"; LOOP=""

# ------------------------------------------------------------------ compress
say "Compressing..."
rm -f "$OUT/clinicjam-$VERSION.img.xz"
xz -T0 -6 -c "$IMG" > "$OUT/clinicjam-$VERSION.img.xz"
rm -f "$IMG"
( cd "$OUT" && sha256sum "clinicjam-$VERSION.img.xz" > "clinicjam-$VERSION.img.xz.sha256" )
say "Done: $OUT/clinicjam-$VERSION.img.xz ($(du -h "$OUT/clinicjam-$VERSION.img.xz" | cut -f1))"
