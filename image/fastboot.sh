#!/bin/bash
# Boot faster: everything here is for a Pi with no screen, no keyboard and no Bluetooth, whose only
# job is to get pianod up. Shared by build.sh (a new image) and update-card.sh (a written card).
#
#   image/fastboot.sh <root mountpoint> <boot mountpoint>
#
# Measured on the Pi before (systemd-analyze, 2026-10-30): kernel 2.9 s + userspace 9.8 s, pianod
# up at ~12.5 s; plus, before Linux, the bootloader trying USB before the SD card - and the badge
# is a USB drive.
set -euo pipefail
ROOT=${1:?usage: fastboot.sh <root> <boot>}
BOOT=${2:?usage: fastboot.sh <root> <boot>}
HERE=$(cd "$(dirname "$0")" && pwd)
CONFIG=$BOOT/config.txt
CMDLINE=$BOOT/cmdline.txt

# --- firmware (config.txt) -------------------------------------------------------------------
# No camera or display to look for, no splash, no Bluetooth (it also frees the UART), and no KMS
# graphics driver for a screen that isn't there.
sed -i -E 's/^camera_auto_detect=1/camera_auto_detect=0/; s/^display_auto_detect=1/display_auto_detect=0/' "$CONFIG"
sed -i -E 's/^dtoverlay=vc4-kms-v3d.*/#&  # pianopi: no screen/' "$CONFIG"
if ! grep -q '^# pianopi fastboot' "$CONFIG"; then
  printf '\n[all]\n# pianopi fastboot\ndisable_splash=1\nboot_delay=0\ndtoverlay=disable-bt\n' >> "$CONFIG"
fi

# --- kernel (cmdline.txt, one line) ------------------------------------------------------------
# No console on the screen (there is none; printing to it is slow); serial0 stays for rescue.
sed -i -E 's/ console=tty1//; s/ quiet//; s/ loglevel=[0-9]//; s/$/ quiet loglevel=3/' "$CMDLINE"

# --- userspace ---------------------------------------------------------------------------------
# Off: what a headless piano doesn't use. Masked rather than disabled where something else would
# pull them in.
systemctl --root="$ROOT" disable bluetooth.service console-setup.service keyboard-setup.service \
  rpi-eeprom-update.service e2scrub_reap.service udisks2.service dpkg-db-backup.timer \
  e2scrub_all.timer 2>/dev/null || true
systemctl --root="$ROOT" mask systemd-binfmt.service hciuart.service >/dev/null 2>&1 || true

# ldconfig and systemd-update-done run when /etc/.updated or /var/.updated is missing or older
# than /usr. Raspberry Pi OS ships without them, so its first boot does that work and writes them;
# on a read-only root every boot is the first (1.3 s, on pianod's path). Stamp them with /usr's
# time now.
ns=$(stat -c %Y "$ROOT/usr")000000000
for d in etc var; do
  printf '# This file was created by systemd-update-done. Its only\n# purpose is to hold a timestamp of the time this directory\n# was updated. See man:systemd-update-done.service(8).\nTIMESTAMP_NSEC=%s\n' "$ns" \
    > "$ROOT/$d/.updated"
  touch -r "$ROOT/usr" "$ROOT/$d/.updated"
done

# --- the bootloader (EEPROM): the SD card first --------------------------------------------------
# It shipped with BOOT_ORDER=0xf14 (USB mass storage, then SD): it looked for a boot disk on the
# badge before the card. The bootloader flashes pieeprom.upd from the boot partition by itself at
# the next power-on, then renames recovery.bin so it happens once.
FW=$ROOT/usr/lib/firmware/raspberrypi/bootloader-2711/default
bin=$(ls "$FW"/pieeprom-*.bin | sort | tail -1)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
python3 "$ROOT/usr/bin/rpi-eeprom-config" "$bin" > "$tmp/boot.conf"
sed -i -E 's/^BOOT_ORDER=.*/BOOT_ORDER=0xf41/' "$tmp/boot.conf"
grep -q '^BOOT_ORDER=0xf41' "$tmp/boot.conf" || echo 'BOOT_ORDER=0xf41' >> "$tmp/boot.conf"
python3 "$ROOT/usr/bin/rpi-eeprom-config" --config "$tmp/boot.conf" --out "$BOOT/pieeprom.upd" "$bin" >/dev/null
sha256sum "$BOOT/pieeprom.upd" | awk '{print $1}' > "$BOOT/pieeprom.sig"
echo "ts: $(date -u +%s)" >> "$BOOT/pieeprom.sig"
cp "$FW/recovery.bin" "$BOOT/recovery.bin"
rm -f "$BOOT"/RECOVERY.0*
echo "fastboot: $(basename "$bin") with BOOT_ORDER=0xf41 staged; $(tr ' ' '\n' < "$CMDLINE" | grep -c .) kernel args"
