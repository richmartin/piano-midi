#!/usr/bin/env python3
"""Make the panel's fonts: TTF -> 1-bit glyphs -> fonts/<name>.fnt, plus a width table for the
laptop tests.

The badge draws 1-bit glyphs, so each glyph is rendered here exactly as the mockups were (PIL,
fontmode "1") and stored as a bitmap: what you see in the mockups is what the badge shows.

The .fnt format is the panel's own (ui.Font reads it), made for 192 KB of RAM: glyphs stay in
flash and are read one at a time, a byte per pixel, ready for bitmaptools.arrayblit.
    header  b"PF1\0", ascent u8, descent u8, glyph count u16
    table   per glyph, by codepoint: cp u16, w u8, h u8, xoff i8, yoff i8, advance u8, data offset u32
    data    w*h bytes per glyph, 0 or 1; yoff is from the baseline up to the glyph's bottom

    python3 badge/tools/mkfonts.py <dir with the TTFs>

The TTFs (Google Fonts, OFL) aren't kept in the repo; the generated fonts are:
    EBGaramond[wght].ttf, BarlowCondensed-Medium.ttf, BarlowCondensed-SemiBold.ttf,
    Silkscreen-Regular.ttf
"""
import json
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "CIRCUITPY", "fonts")
WIDTHS = os.path.join(HERE, "..", "tests", "font_widths.json")

# name: (file, pixel size, variable-font instance)
FONTS = {
    "title": ("EBGaramond[wght].ttf", 16, b"SemiBold"),
    "title_s": ("EBGaramond[wght].ttf", 14, b"SemiBold"),
    "big": ("EBGaramond[wght].ttf", 24, b"SemiBold"),
    "body": ("BarlowCondensed-Medium.ttf", 14, None),
    "body_b": ("BarlowCondensed-SemiBold.ttf", 15, None),
    "label": ("Silkscreen-Regular.ttf", 8, None),
}
# ASCII, Latin-1 (accented names), and the typography the catalogue uses.
CHARS = ([chr(c) for c in range(32, 127)] + [chr(c) for c in range(160, 256)]
         + list("–—‘’“”…·•ŒœŠšŽžŸıłŁőŐűŰčČřŘěĚňŇťŤďĎůŮśŚźŹżŻćĆńŃęĘąĄ"))


def glyph(font, ch, asc):
    pad = 8
    size = font.size * 3
    img = Image.new("1", (size + 2 * pad, size + 2 * pad), 0)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    d.text((pad, pad + asc), ch, font=font, fill=1, anchor="ls")
    advance = round(font.getlength(ch))
    box = img.getbbox()
    if not box:
        return advance, 0, 0, 0, 0, b""
    left, top, right, bottom = box
    w, h = right - left, bottom - top
    data = bytes(1 if img.getpixel((x, y)) else 0 for y in range(top, bottom) for x in range(left, right))
    # offsets from the origin (baseline, pen position) to the bitmap's bottom-left
    return advance, w, h, left - pad, (pad + asc) - bottom, data


def make(name, path, size, var):
    font = ImageFont.truetype(path, size)
    if var:
        font.set_variation_by_name(var)
    asc, desc = font.getmetrics()
    missing = glyph(font, "\uffff", asc)[1:]       # what the font draws for a glyph it lacks
    glyphs = []
    for ch in CHARS:
        try:
            g = glyph(font, ch, asc)
        except (OSError, ValueError):
            continue
        if ch != " " and (not g[5] or g[1:] == missing):
            continue                      # the font doesn't have it
        glyphs.append((ch, g))
    glyphs.sort(key=lambda cg: ord(cg[0]))
    table, data = b"", b""
    base = 8 + 11 * len(glyphs)
    for ch, (adv, w, h, xoff, yoff, bits) in glyphs:
        table += struct.pack("<HBBbbBI", ord(ch), w, h, xoff, yoff, adv, base + len(data))
        data += bits
    with open(os.path.join(OUT, f"{name}.fnt"), "wb") as f:
        f.write(b"PF1\0" + struct.pack("<BBH", asc, desc, len(glyphs)) + table + data)
    return {ch: g[0] for ch, g in glyphs}, asc, desc


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(OUT, exist_ok=True)
    widths = {}
    for name, (file, size, var) in FONTS.items():
        adv, asc, desc = make(name, os.path.join(src, file), size, var)
        widths[name] = {"ascent": asc, "descent": desc, "advance": adv}
        print(f"{name:8} {file} {size}px: {len(adv)} glyphs, ascent {asc}, descent {desc}")
    with open(WIDTHS, "w") as f:
        json.dump(widths, f, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    main()
