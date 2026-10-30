#!/bin/bash
# Update pianod on the running Pi over SSH: no card, no maintenance-mode reboot.
#
#   image/update-pi.sh [rich@pianopi.fam-mart.in] [--force]
#
# The root is read-only (overlayroot), so pianod goes into the layer underneath
# (/media/root-ro, remounted read-write for the copy), and the Pi reboots onto it (~25 s).
# Refuses while the piano is playing, unless --force. Needs rich's passwordless sudo
# (image/files/sudoers-piano).
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
HOST=rich@pianopi.fam-mart.in
FORCE=no
for a in "$@"; do
  case "$a" in --force) FORCE=yes ;; *) HOST=$a ;; esac
done
[ -f "$REPO/pianod/pianod/daemon.py" ] || { echo "no pianod at $REPO/pianod" >&2; exit 1; }
( cd "$REPO" && PATH=$REPO/.venv/bin:$PATH python3 -m pytest -q pianod/tests >/dev/null ) \
  || { echo "pianod's tests fail; not updating" >&2; exit 1; }
VERSION="pianod $(git -C "$REPO" describe --always --dirty) (updated over SSH $(date -Is))"

tar -C "$REPO" --exclude=__pycache__ -cf - pianod image/files/pianod.service | ssh "$HOST" "
  set -e
  if [ $FORCE = no ] && curl -s -m 3 http://127.0.0.1:8080/api/state | grep -q '\"playing\":true'; then
    echo 'the piano is playing; not now (--force to anyway)' >&2; exit 1
  fi
  tmp=\$(mktemp -d); trap 'rm -rf \$tmp' EXIT
  tar -C \$tmp -xf -
  # Into the layer under the overlay, then a reboot to see it. Never restart onto it in place:
  # changing the lower layer under a mounted overlay is undefined (kernel overlayfs docs), and
  # in practice the running system lost sight of pianod altogether.
  sudo mount -o remount,rw /media/root-ro
  L=/media/root-ro
  sudo rm -rf \$L/opt/piano/pianod.new
  sudo cp -r \$tmp/pianod \$L/opt/piano/pianod.new
  sudo rm -rf \$L/opt/piano/pianod
  sudo mv \$L/opt/piano/pianod.new \$L/opt/piano/pianod
  sudo install -m 644 \$tmp/image/files/pianod.service \$L/etc/systemd/system/pianod.service
  { head -1 \$L/etc/pianopi-build; echo '$VERSION'; } | sudo tee \$L/etc/pianopi-build.new >/dev/null
  sudo mv \$L/etc/pianopi-build.new \$L/etc/pianopi-build
  sudo sync
  sudo systemctl reboot
" 2>&1 | grep -v "rptl.io\|newuser\|SSH may not work\|^$" || true

# It comes back read-only, on the new pianod (about 25 s).
api="http://${HOST#*@}:8080/api/state"
sleep 5
for i in $(seq 60); do
  curl -s -m 2 "$api" >/dev/null 2>&1 && break
  sleep 2
done
ssh "$HOST" 'echo "now: $(tail -1 /etc/pianopi-build); pianod $(systemctl is-active pianod); root layer $(findmnt -no OPTIONS /media/root-ro | cut -d, -f1)"' \
  2>&1 | grep -v "rptl.io\|newuser\|SSH may not work\|^$"
