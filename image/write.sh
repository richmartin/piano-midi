#!/bin/bash
# Write the built image (image/work/pianopi.img) to an SD card.
#
#   sudo image/write.sh /dev/sdX
#
# Shows the card and asks before erasing it. Refuses anything that isn't a removable disk.
set -euo pipefail

IMG=$(cd "$(dirname "$0")" && pwd)/work/pianopi.img
DEV=${1:?usage: sudo image/write.sh /dev/sdX}

die() { printf 'write.sh: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run with sudo"
[ -s "$IMG" ] || die "no image at $IMG (run image/build.sh first)"
[ -b "$DEV" ] || die "$DEV isn't a block device"
[ "$(lsblk -dno TYPE "$DEV" | tr -d " ")" = disk ] || die "$DEV is a partition; give the whole card (e.g. /dev/sda)"
[ "$(lsblk -dno RM "$DEV" | tr -d " ")" = 1 ] || die "$DEV isn't removable; refusing"
img_bytes=$(stat -c %s "$IMG"); dev_bytes=$(blockdev --getsize64 "$DEV")
[ "$dev_bytes" -ge "$img_bytes" ] || die "$DEV is smaller than the image"

echo "Image: $IMG ($(du -h "$IMG" | cut -f1), built $(date -r "$IMG" '+%Y-%m-%d %H:%M'))"
echo
lsblk -o NAME,SIZE,MODEL,LABEL,MOUNTPOINTS "$DEV"
echo
read -r -p "Erase everything on $DEV and write PianoPi to it? Type 'yes': " ok
[ "$ok" = yes ] || die "not written"

for p in $(lsblk -lno PATH "$DEV" | tail -n +2); do umount "$p" 2>/dev/null || true; done
dd if="$IMG" of="$DEV" bs=4M conv=fsync status=progress
sync
echo "Checking what was written..."
cmp -n "$img_bytes" "$IMG" "$DEV" && echo "OK: the card matches the image. It can go in the Pi."
