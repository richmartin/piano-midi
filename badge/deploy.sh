#!/bin/bash
# Copy the panel's files onto the PyBadge's CIRCUITPY drive. It reloads by itself.
#
#   badge/deploy.sh                    the badge is plugged into this machine (CIRCUITPY mounted)
#   badge/deploy.sh rich@pianopi.local the badge is on the Pi; go through it over SSH
#                                      (needs the image's CIRCUITPY fstab entry)
#
# Runs the layout tests first: the badge is usually on the piano, where nobody is watching it.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
FILES=(boot.py code.py layout.py texts.py)

python3 -m pytest -q "$HERE/tests" >/dev/null || { echo "layout tests fail; not deploying" >&2; exit 1; }
python3 -m py_compile "$HERE"/CIRCUITPY/*.py

if [ $# -eq 0 ]; then
  dest=$(findmnt -rno TARGET -S LABEL=CIRCUITPY | head -1)
  [ -n "$dest" ] || { echo "no CIRCUITPY drive mounted here" >&2; exit 1; }
  for f in "${FILES[@]}"; do cp "$HERE/CIRCUITPY/$f" "$dest/"; done
  cp -r "$HERE/CIRCUITPY/lib/." "$dest/lib/"
  sync
  echo "deployed to $dest"
else
  host=$1
  tar -C "$HERE/CIRCUITPY" -cf - "${FILES[@]}" lib | ssh "$host" '
    set -e
    tmp=$(mktemp -d); trap "rm -rf $tmp" EXIT
    tar -C "$tmp" -xf -
    dev=$(readlink -f /dev/disk/by-label/CIRCUITPY)
    [ -b "$dev" ] || { echo "no CIRCUITPY drive on $(hostname): is the badge plugged in?" >&2; exit 1; }
    # Mountable without sudo through the image'\''s fstab entry.
    mount /mnt/circuitpy
    cp -r "$tmp"/. /mnt/circuitpy/
    sync; umount /mnt/circuitpy
    echo "deployed to the badge on $(hostname)"'
fi
