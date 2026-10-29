#!/usr/bin/env python3
"""Make the panel's bitmap fonts: TTF -> 1-bit glyphs -> BDF -> PCF (bdftopcf), plus a width
table for the laptop tests.

The badge draws 1-bit glyphs, so each glyph is rendered here exactly as the mockups were (PIL,
fontmode "1") and stored as a bitmap: what you see in the mockups is what the badge shows.

    python3 badge/tools/mkfonts.py <dir with the TTFs>

The TTFs (Google Fonts, OFL) aren't kept in the repo; the generated fonts are:
    EBGaramond[wght].ttf, BarlowCondensed-Medium.ttf, BarlowCondensed-SemiBold.ttf,
    Silkscreen-Regular.ttf
"""
import json
import os
import subprocess
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
        return advance, 1, 1, 0, 0, ["00"]     # a blank pixel: PCF readers choke on 0x0
    left, top, right, bottom = box
    w, h = right - left, bottom - top
    rows = []
    for y in range(top, bottom):
        bits = 0
        for x in range(left, right):
            bits = (bits << 1) | (1 if img.getpixel((x, y)) else 0)
        nbytes = (w + 7) // 8
        bits <<= nbytes * 8 - w
        rows.append(f"{bits:0{nbytes * 2}X}")
    # BDF offsets: from the origin (baseline, pen position) to the bitmap's bottom-left.
    return advance, w, h, left - pad, (pad + asc) - bottom, rows


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
        if ch != " " and (g[5] == ["00"] or g[1:] == missing):
            continue                      # the font doesn't have it
        glyphs.append((ch, g))
    lines = ["STARTFONT 2.1", f"FONT piano-{name}", f"SIZE {size} 75 75",
             f"FONTBOUNDINGBOX {size * 2} {asc + desc} 0 {-desc}",
             "STARTPROPERTIES 2", f"FONT_ASCENT {asc}", f"FONT_DESCENT {desc}", "ENDPROPERTIES",
             f"CHARS {len(glyphs)}"]
    for ch, (adv, w, h, xoff, yoff, rows) in glyphs:
        lines += [f"STARTCHAR U+{ord(ch):04X}", f"ENCODING {ord(ch)}", f"SWIDTH {adv * 1000 // size} 0",
                  f"DWIDTH {adv} 0", f"BBX {w} {h} {xoff} {yoff}", "BITMAP", *rows, "ENDCHAR"]
    lines.append("ENDFONT")
    bdf = os.path.join(OUT, f"{name}.bdf")
    with open(bdf, "w") as f:
        f.write("\n".join(lines) + "\n")
    subprocess.run(["bdftopcf", "-o", os.path.join(OUT, f"{name}.pcf"), bdf], check=True)
    os.remove(bdf) if not os.environ.get("KEEP_BDF") else None
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
