#!/bin/bash
# Update pianod on a PianoPi card that's already written, without rebuilding the image: the
# library, recordings, calibration and saved networks on its data partition stay as they are.
#
#   sudo image/update-card.sh /dev/sdX
#
# For when the Pi can't be reached over SSH (no network yet). Over SSH it's simpler to copy
# /opt/piano/pianod in maintenance mode (piano-maint on).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
DEV=${1:?usage: sudo image/update-card.sh /dev/sdX}
die() { printf 'update-card.sh: %s\n' "$*" >&2; exit 1; }
[ -f "$REPO/pianod/pianod/daemon.py" ] || die "no pianod at $REPO/pianod"
[ "$(id -u)" = 0 ] || die "run with sudo"
[ "$(lsblk -dno RM "$DEV" | tr -d " ")" = 1 ] || die "$DEV isn't removable; refusing"
ROOT=$(lsblk -lno PATH,LABEL "$DEV" | awk '$2=="rootfs"{print $1}')
DATA=$(lsblk -lno PATH,LABEL "$DEV" | awk '$2=="piano-data"{print $1}')
[ -n "$ROOT" ] && [ -n "$DATA" ] || die "$DEV doesn't look like a PianoPi card (no rootfs / piano-data)"

MNT=$(mktemp -d)
cleanup() { umount "$MNT/data" 2>/dev/null || true; umount "$MNT/root" 2>/dev/null || true; rm -rf "$MNT"; }
trap cleanup EXIT
mkdir -p "$MNT/root" "$MNT/data"
for p in "$ROOT" "$DATA"; do umount "$p" 2>/dev/null || true; done
mount "$ROOT" "$MNT/root"
mount "$DATA" "$MNT/data"
[ -d "$MNT/root/opt/piano/pianod" ] || die "no /opt/piano/pianod on $ROOT"

echo "was: $(tail -1 "$MNT/root/etc/pianopi-build")"
# Beside the old one first, then swap: a failed copy leaves the old pianod in place.
rm -rf "$MNT/root/opt/piano/pianod.new"
cp -r "$REPO/pianod" "$MNT/root/opt/piano/pianod.new"
find "$MNT/root/opt/piano/pianod.new" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$MNT/root/opt/piano/pianod"
mv "$MNT/root/opt/piano/pianod.new" "$MNT/root/opt/piano/pianod"
install -m 644 "$REPO/image/files/pianod.service" "$MNT/root/etc/systemd/system/pianod.service"
{ head -1 "$MNT/root/etc/pianopi-build"
  echo "pianod $(git -C "$REPO" describe --always --dirty 2>/dev/null) (updated on the card $(date -Is))"; } \
  > "$MNT/root/etc/pianopi-build.new" && mv "$MNT/root/etc/pianopi-build.new" "$MNT/root/etc/pianopi-build"
echo "now: $(tail -1 "$MNT/root/etc/pianopi-build")"

# Saved networks made before PMF was turned off for them (see build.sh).
for f in "$MNT/data/nm"/*.nmconnection; do
  [ -e "$f" ] || continue
  if grep -q '^key-mgmt=wpa-psk' "$f" && ! grep -q '^pmf=' "$f"; then
    sed -i '/^key-mgmt=wpa-psk/a pmf=1' "$f"
    echo "PMF off for $(basename "$f" .nmconnection)"
  fi
done
sync
echo "done: put the card back in the Pi"
