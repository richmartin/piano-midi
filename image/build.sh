#!/bin/bash
# Build the PianoPi SD-card image from the official Raspberry Pi OS Lite image, on a laptop.
# Adapted from hotpi's image/build.sh (see hotpi/docs/PyBadge_Pi_Learnings.md for the why of
# almost every line).
#
#   sudo image/build.sh             -> image/work/pianopi.img
#   sudo image/build.sh --write /dev/sdX   ... and write it to that card (asks first)
#   sudo SEED=<pianod data dir> image/build.sh   ... starting with that dir's library mirror and
#                                       calibration (an hour at the piano to measure again)
#
# Everything the Pi needs is installed here, in an arm64 chroot under qemu (qemu-user-static +
# binfmt-support), so the card works on first boot with no keyboard and no screen:
#   - partitions: boot, root (read-only at run time via overlayroot), and an ext4 data partition
#     for the library mirror, recordings, state, saved WiFi networks and the saved clock;
#   - the account (password hash from $HASH_FILE, never in the repo), SSH keys, host keys and
#     machine-id generated now (on a read-only root they would change every boot);
#   - pianod as a systemd service, the hardware watchdog, logs in RAM;
#   - first-boot machinery off: no userconfig prompt, no cloud-init, no auto-resize.
#
# The Pi is NOT powered through the piano's smart plug: it stays on, and the read-only root means
# even a wall-switch mishap can't corrupt the card.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "$HERE")
OWNER=${SUDO_USER:-rich}
OWNER_HOME=$(getent passwd "$OWNER" | cut -d: -f6)

BASE_URL=https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06
BASE_XZ=2026-10-06-raspios-trixie-arm64-lite.img.xz
BASE_SHA256=483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843

HOSTNAME=pianopi
USERNAME=rich
HASH_FILE=${HASH_FILE:-$OWNER_HOME/.config/pianopi-rich.hash}
PUBKEYS_DIR=${PUBKEYS_DIR:-$OWNER_HOME/dotfiles/ssh/pubkeys}
PUBKEYS=(thinker omen murdoch pocket)          # ssh/authorized_keys.policy: "all"
# WiFi networks the Pi joins by itself. The hotspot gets it on its feet anywhere; add the house
# WiFi from a shell once it's at the piano (nmcli device wifi connect ...), which lands on the
# data partition and sticks.
WIFI_NETWORKS=("Excession:hollywood")
TIMEZONE=Europe/Zurich

ROOT_MIB=4096       # the root partition; Lite + our packages use ~2.5 GiB
DATA_MIB=1024       # the data partition: the library is ~14 MB, recordings are tiny
CACHE=$HERE/cache
WORK=$HERE/work
IMG=$WORK/pianopi.img
MNT=$WORK/mnt

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'build.sh: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run with sudo"
NO_PASSWORD=no
if [ "${1:-}" = "--no-password" ]; then NO_PASSWORD=yes; shift; fi   # test builds: password locked
if [ $NO_PASSWORD = no ]; then
  [ -s "$HASH_FILE" ] || die "no password hash at $HASH_FILE (umask 077; openssl passwd -6 > $HASH_FILE)"
  grep -q '^\$6\$' "$HASH_FILE" || die "$HASH_FILE doesn't look like an openssl passwd -6 hash"
fi
[ -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ] || die "no qemu-aarch64 binfmt (install qemu-user-static binfmt-support)"
for k in "${PUBKEYS[@]}"; do [ -s "$PUBKEYS_DIR/$k.pub" ] || die "missing $PUBKEYS_DIR/$k.pub"; done

LOOP=""
cleanup() {
  set +e
  for m in "$MNT/var/lib/piano" "$MNT/boot/firmware" "$MNT/dev/pts" "$MNT/dev" "$MNT/proc" "$MNT/sys" "$MNT/run" "$MNT"; do
    mountpoint -q "$m" && umount -l "$m"
  done
  [ -n "$LOOP" ] && losetup -d "$LOOP"
}
trap cleanup EXIT

# --- 1. the base image, checked ------------------------------------------------------------
mkdir -p "$CACHE" "$WORK"
if [ ! -s "$CACHE/$BASE_XZ" ]; then
  say "downloading $BASE_XZ"
  curl -fL -o "$CACHE/$BASE_XZ.part" "$BASE_URL/$BASE_XZ" && mv "$CACHE/$BASE_XZ.part" "$CACHE/$BASE_XZ"
fi
echo "$BASE_SHA256  $CACHE/$BASE_XZ" | sha256sum -c --quiet || die "$BASE_XZ doesn't match its SHA-256"

say "unpacking"
xz -dc "$CACHE/$BASE_XZ" > "$IMG"

# --- 2. partitions: boot | root (fixed size) | data ----------------------------------------
say "partitioning: root ${ROOT_MIB} MiB, data ${DATA_MIB} MiB"
P2_START=$(parted -sm "$IMG" unit MiB print | awk -F: '$1==2{sub("MiB","",$2); print int($2)}')
TOTAL=$((P2_START + ROOT_MIB + DATA_MIB + 4))
truncate -s "${TOTAL}M" "$IMG"
parted -s "$IMG" resizepart 2 "$((P2_START + ROOT_MIB))MiB"
parted -s "$IMG" mkpart primary ext4 "$((P2_START + ROOT_MIB))MiB" "$((P2_START + ROOT_MIB + DATA_MIB))MiB"
LOOP=$(losetup -fP --show "$IMG")
e2fsck -pf "${LOOP}p2" >/dev/null || [ $? -le 1 ]
resize2fs -p "${LOOP}p2" >/dev/null
# With a journal: that is what makes a power cut mid-write safe.
mkfs.ext4 -q -F -L piano-data "${LOOP}p3"

# --- 3. mount and chroot ---------------------------------------------------------------------
say "mounting"
mkdir -p "$MNT"
mount "${LOOP}p2" "$MNT"
mount "${LOOP}p1" "$MNT/boot/firmware"
mkdir -p "$MNT/var/lib/piano"
mount "${LOOP}p3" "$MNT/var/lib/piano"
for d in dev dev/pts proc sys run; do mount --bind "/$d" "$MNT/$d"; done
cp /etc/resolv.conf "$MNT/etc/resolv.conf.build"
mount --bind "$MNT/etc/resolv.conf.build" "$MNT/etc/resolv.conf" 2>/dev/null || cp /etc/resolv.conf "$MNT/etc/resolv.conf"

in_pi() { chroot "$MNT" /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 "$@"; }

say "packages (this is the slow part: an ARM CPU emulated)"
in_pi apt-get -q update
in_pi apt-get -q -y --no-install-recommends install overlayroot python3-serial python3-mido python3-rtmidi alsa-utils
in_pi apt-get -q -y purge cloud-init modemmanager 2>/dev/null || true
in_pi apt-get -q -y autoremove --purge
in_pi apt-get -q clean
umount "$MNT/etc/resolv.conf" 2>/dev/null || true
rm -f "$MNT/etc/resolv.conf.build"

# --- 4. the system -------------------------------------------------------------------------------
say "system settings"
echo "$HOSTNAME" > "$MNT/etc/hostname"
sed -i "s/127.0.1.1.*/127.0.1.1\t$HOSTNAME/" "$MNT/etc/hosts"
grep -q "127.0.1.1" "$MNT/etc/hosts" || printf '127.0.1.1\t%s\n' "$HOSTNAME" >> "$MNT/etc/hosts"
ln -sf "/usr/share/zoneinfo/$TIMEZONE" "$MNT/etc/localtime"
echo "$TIMEZONE" > "$MNT/etc/timezone"

# The account. The password hash comes from outside the repo; SSH keys are rich's own.
in_pi useradd -m -s /bin/bash -G sudo,dialout,netdev,audio,video "$USERNAME" 2>/dev/null || true
if [ $NO_PASSWORD = yes ]; then in_pi usermod -L "$USERNAME"; else in_pi usermod -p "$(cat "$HASH_FILE")" "$USERNAME"; fi
UIDN=$(in_pi id -u "$USERNAME"); GIDN=$(in_pi id -g "$USERNAME")
# files/fstab.append mounts the badge's drive as uid/gid 1001; keep that true.
[ "$UIDN:$GIDN" = 1001:1001 ] || die "$USERNAME is $UIDN:$GIDN, but fstab.append expects 1001:1001"
install -d "$MNT/mnt/circuitpy"
install -d -m 700 -o "$UIDN" -g "$GIDN" "$MNT/home/$USERNAME/.ssh"
for k in "${PUBKEYS[@]}"; do cat "$PUBKEYS_DIR/$k.pub"; done > "$MNT/home/$USERNAME/.ssh/authorized_keys"
chown "$UIDN:$GIDN" "$MNT/home/$USERNAME/.ssh/authorized_keys"; chmod 600 "$MNT/home/$USERNAME/.ssh/authorized_keys"

# Generated once, here: on a read-only root they would be new on every boot.
rm -f "$MNT"/etc/ssh/ssh_host_*
in_pi ssh-keygen -A
: > "$MNT/etc/machine-id"; in_pi systemd-machine-id-setup >/dev/null

# First-boot machinery off: no username prompt, no cloud-init, no auto-resize, SSH on.
in_pi systemctl disable userconfig.service regenerate_ssh_host_keys.service rpi-resize.service \
  rpi-resize-swap-file.service apt-daily.timer apt-daily-upgrade.timer man-db.timer 2>/dev/null || true
mkdir -p "$MNT/etc/cloud"; touch "$MNT/etc/cloud/cloud-init.disabled"
in_pi systemctl enable ssh.service
sed -i 's/ resize//; s/ init=[^ ]*//' "$MNT/boot/firmware/cmdline.txt"

# Read-only root (overlayroot), the data partition, logs in RAM, the watchdog.
F=$HERE/files
cat "$F/fstab.append" >> "$MNT/etc/fstab"
sed -i -E 's#^(\S+\s+/boot/firmware\s+vfat\s+)defaults#\1defaults,ro#' "$MNT/etc/fstab"
install -D -m 644 "$F/journald-volatile.conf" "$MNT/etc/systemd/journald.conf.d/piano-volatile.conf"
install -D -m 644 "$F/watchdog.conf" "$MNT/etc/systemd/system.conf.d/piano-watchdog.conf"
# The saved clock: systemd-timesyncd sets the clock forward to its clock file's timestamp when it
# starts, network or not, and refreshes the stamp as it runs. The file has to survive a read-only
# root, so its directory comes from the data partition (fstab bind mount).
install -D -m 644 "$F/timesyncd.conf" "$MNT/etc/systemd/timesyncd.conf.d/piano.conf"
install -D -m 644 "$F/timesyncd-after-data.conf" "$MNT/etc/systemd/system/systemd-timesyncd.service.d/piano.conf"
grep -q overlayroot= "$MNT/boot/firmware/cmdline.txt" || sed -i 's/^/overlayroot=tmpfs:recurse=0 /' "$MNT/boot/firmware/cmdline.txt"
in_pi update-initramfs -u -k all

# The data partition's starting contents. The library mirror starts empty: the first
# `piano sync` (or the badge's "Update the library") fills it from the server.
install -d -m 755 "$MNT/var/lib/piano" "$MNT/var/lib/piano/midi-files" "$MNT/var/lib/piano/recordings"
if [ -n "${SEED:-}" ]; then
  say "seeding the data partition from $SEED"
  [ -s "$SEED/library.json" ] && cp "$SEED/library.json" "$MNT/var/lib/piano/"
  [ -d "$SEED/midi-files" ] && cp -r "$SEED/midi-files/." "$MNT/var/lib/piano/midi-files/"
  for f in "$SEED"/calibration*.json; do [ -e "$f" ] && cp "$f" "$MNT/var/lib/piano/"; done
  ls "$MNT/var/lib/piano"; du -sh "$MNT/var/lib/piano"
fi
install -d -m 700 "$MNT/var/lib/piano/nm"
TSU=$(in_pi id -u systemd-timesync); TSG=$(in_pi id -g systemd-timesync)
install -d -m 755 -o "$TSU" -g "$TSG" "$MNT/var/lib/piano/timesync"
touch "$MNT/var/lib/piano/timesync/clock"            # its mtime: the build date
chown "$TSU:$TSG" "$MNT/var/lib/piano/timesync/clock"
for net in "${WIFI_NETWORKS[@]}"; do
  ssid=${net%%:*}; psk=${net#*:}
  cat > "$MNT/var/lib/piano/nm/$ssid.nmconnection" <<EOF
[connection]
id=$ssid
type=wifi
autoconnect=true

[wifi]
mode=infrastructure
ssid=$ssid

[wifi-security]
key-mgmt=wpa-psk
psk=$psk
# Management-frame protection off: the Pi 4's brcmfmac often fails to join WPA2/WPA3
# transition networks (phone hotspots) when NetworkManager offers it.
pmf=1

[ipv4]
method=auto

[ipv6]
method=auto
EOF
  chmod 600 "$MNT/var/lib/piano/nm/$ssid.nmconnection"
done
# NetworkManager needs a country for the WiFi radio to transmit.
mkdir -p "$MNT/etc/NetworkManager/conf.d"
printf '[device]\nwifi.scan-rand-mac-address=no\n' > "$MNT/etc/NetworkManager/conf.d/piano.conf"
# Raspberry Pi OS keeps WiFi rfkill-blocked until a country is set; the saved unblock would not
# survive a read-only root, so unblock at every boot.
install -m 644 "$F/piano-rfkill.service" "$MNT/etc/systemd/system/piano-rfkill.service"
in_pi systemctl enable piano-rfkill.service
sed -i 's/ cfg80211.ieee80211_regdom=[^ ]*//; s/$/ cfg80211.ieee80211_regdom=CH/' "$MNT/boot/firmware/cmdline.txt"

# --- 5. pianod ---------------------------------------------------------------------------------
say "pianod"
rm -rf "$MNT/opt/piano"; install -d "$MNT/opt/piano"
cp -r "$REPO/pianod" "$MNT/opt/piano/pianod"
find "$MNT/opt/piano" -name __pycache__ -prune -exec rm -rf {} +
ln -sf /opt/piano/pianod/bin/piano "$MNT/usr/local/bin/piano"
install -m 755 "$F/piano-maint" "$MNT/usr/local/sbin/piano-maint"
install -m 644 "$F/pianod.service" "$MNT/etc/systemd/system/pianod.service"
in_pi systemctl enable pianod.service
# A record of what went onto the card.
{ echo "built $(date -Is) from $BASE_XZ"; echo "pianod $(git -C "$REPO" describe --always --dirty 2>/dev/null)"; } \
  > "$MNT/etc/pianopi-build"

say "checks"
grep -o 'overlayroot=[^ ]*' "$MNT/boot/firmware/cmdline.txt"
in_pi python3 -c "import mido, rtmidi, serial; print('python modules ok')"
for u in pianod piano-rfkill ssh systemd-timesyncd NetworkManager; do
  [ "$(in_pi systemctl is-enabled $u 2>/dev/null)" = enabled ] || die "$u isn't enabled"
done
for u in userconfig regenerate_ssh_host_keys rpi-resize; do
  [ "$(in_pi systemctl is-enabled $u 2>/dev/null)" != enabled ] || die "$u is still enabled"
done
echo "services ok"
in_pi python3 -c "import sys; sys.path.insert(0, '/opt/piano/pianod'); import pianod.daemon; print('pianod imports ok')"
ls -la "$MNT/boot/firmware"/initramfs*
cat "$MNT/boot/firmware/cmdline.txt"

cleanup; trap - EXIT; LOOP=""
chown "$OWNER:" "$IMG"
say "built $IMG ($(du -h "$IMG" | cut -f1))"

# --- 6. optionally, onto a card ----------------------------------------------------------------
if [ "${1:-}" = "--write" ]; then
  DEV=${2:?usage: build.sh --write /dev/sdX}
  [ -b "$DEV" ] || die "$DEV isn't a block device"
  [ "$(lsblk -dno RM "$DEV" | tr -d " ")" = 1 ] || die "$DEV isn't removable; refusing"
  lsblk -o NAME,SIZE,MODEL,LABEL,MOUNTPOINTS "$DEV"
  read -r -p "Erase $DEV and write PianoPi to it? Type 'yes': " ok
  [ "$ok" = yes ] || die "not written"
  for p in $(lsblk -lno PATH "$DEV" | tail -n +2); do umount "$p" 2>/dev/null || true; done
  dd if="$IMG" of="$DEV" bs=4M conv=fsync status=progress
  sync
  say "written to $DEV"
fi
