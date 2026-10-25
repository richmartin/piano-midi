#!/usr/bin/env python3
"""Turn a UF2 file into a flat binary plus its start address, for bossac.

UF2 is 512-byte blocks, each carrying up to 476 bytes of payload and the flash address it belongs
at. This writes the payload in address order, filling any gaps with 0xff (erased flash).
Usage: uf2tobin.py firmware.uf2 firmware.bin   -> prints the start address, e.g. 0x4000
"""
import struct
import sys

MAGIC0, MAGIC1, MAGIC_END = 0x0A324655, 0x9E5D5157, 0x0AB16F30
FLAG_NOT_MAIN_FLASH = 0x00000001

blocks = {}
data = open(sys.argv[1], "rb").read()
for off in range(0, len(data), 512):
    b = data[off:off + 512]
    m0, m1, flags, addr, size = struct.unpack_from("<IIIII", b, 0)
    if m0 != MAGIC0 or m1 != MAGIC1 or struct.unpack_from("<I", b, 508)[0] != MAGIC_END:
        sys.exit(f"block at {off} isn't a UF2 block")
    if flags & FLAG_NOT_MAIN_FLASH:
        continue
    blocks[addr] = b[32:32 + size]

start = min(blocks)
end = max(a + len(p) for a, p in blocks.items())
image = bytearray(b"\xff" * (end - start))
for addr, payload in blocks.items():
    image[addr - start:addr - start + len(payload)] = payload
open(sys.argv[2], "wb").write(image)
print(hex(start))
