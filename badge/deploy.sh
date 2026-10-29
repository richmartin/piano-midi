#!/bin/bash
# Copy the panel onto the PyBadge's CIRCUITPY drive. It reloads by itself.
#
#   badge/deploy.sh                    the badge is plugged into this machine (CIRCUITPY mounted)
#   badge/deploy.sh rich@pianopi.local the badge is on the Pi; go through it over SSH
#                                      (needs the image's CIRCUITPY fstab entry)
#
# Runs the layout tests first: the badge is usually on the piano, where nobody is watching it.
# The panel's modules go over compiled (.mpy): CircuitPython compiles a .py in RAM at import,
# and with the fonts loaded the panel needs that RAM. mpy-cross must match the badge's
# CircuitPython; it's fetched once, checked against its SHA-256.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PLAIN=(boot.py code.py)                     # CircuitPython runs these two as .py
COMPILED=(app ui layout texts)
CP_VERSION=10.3.1
MPY_CROSS_SHA256=aa74dcb51e51c1a70fd268dad9e05b40f72c29df3082f86c3a20665360e4acda
MPY_CROSS=${XDG_CACHE_HOME:-$HOME/.cache}/piano/mpy-cross-$CP_VERSION

python3 -m pytest -q "$HERE/tests" >/dev/null || { echo "layout tests fail; not deploying" >&2; exit 1; }
python3 -m py_compile "$HERE"/CIRCUITPY/*.py

if [ ! -x "$MPY_CROSS" ]; then
  mkdir -p "$(dirname "$MPY_CROSS")"
  curl -fsSL -o "$MPY_CROSS.part" \
    "https://adafruit-circuit-python.s3.amazonaws.com/bin/mpy-cross/linux-amd64/mpy-cross-linux-amd64-$CP_VERSION.static"
  echo "$MPY_CROSS_SHA256  $MPY_CROSS.part" | sha256sum -c --quiet || { rm -f "$MPY_CROSS.part"; echo "mpy-cross checksum mismatch" >&2; exit 1; }
  chmod +x "$MPY_CROSS.part" && mv "$MPY_CROSS.part" "$MPY_CROSS"
fi
BUILD=$(mktemp -d); trap 'rm -rf "$BUILD"' EXIT
for f in "${PLAIN[@]}"; do cp "$HERE/CIRCUITPY/$f" "$BUILD/"; done
for m in "${COMPILED[@]}"; do "$MPY_CROSS" -o "$BUILD/$m.mpy" "$HERE/CIRCUITPY/$m.py"; done
cp -r "$HERE/CIRCUITPY/fonts" "$BUILD/"
# The same modules as .py would win over the .mpy: remove any left from an older deploy, and
# what the panel no longer uses (the display_text and bitmap_font libraries, PCF fonts).
STALE="$(printf '%s.py ' "${COMPILED[@]}") lib fonts/*.pcf memtest.py"

if [ $# -eq 0 ]; then
  dest=$(findmnt -rno TARGET -S LABEL=CIRCUITPY | head -1)
  [ -n "$dest" ] || { echo "no CIRCUITPY drive mounted here" >&2; exit 1; }
  (cd "$dest" && rm -rf $STALE)
  cp -r "$BUILD"/. "$dest/"
  sync
  echo "deployed to $dest"
else
  host=$1
  tar -C "$BUILD" -cf - . | ssh "$host" "
    set -e
    tmp=\$(mktemp -d); trap 'rm -rf \$tmp' EXIT
    tar -C \$tmp -xf -
    dev=\$(readlink -f /dev/disk/by-label/CIRCUITPY)
    [ -b \"\$dev\" ] || { echo \"no CIRCUITPY drive on \$(hostname): is the badge plugged in?\" >&2; exit 1; }
    # Mountable without sudo through the image's fstab entry.
    mount /mnt/circuitpy
    (cd /mnt/circuitpy && rm -rf $STALE)
    cp -r \$tmp/. /mnt/circuitpy/
    sync; umount /mnt/circuitpy
    echo \"deployed to the badge on \$(hostname)\""
fi
