# The panel's drawing layer, made for 192 KB of RAM.
#
# Everything is drawn into one 160x128 framebuffer (a 16-colour displayio.Bitmap, 10 KB) shown
# by a single TileGrid. A widget is a few numbers: it draws itself into the framebuffer, and when
# it changes, only the area it covered and now covers is repainted (with whatever lies under and
# over it there). The display is refreshed by hand, so a change is never seen half-drawn, and
# displayio sends only the changed area: no flicker. (The first panel rebuilt the whole screen
# for every change; a display object per label, the next attempt, ran out of memory.)
#
# Text comes from the panel's own .fnt files (tools/mkfonts.py), read from flash a glyph at a
# time and copied in with bitmaptools.arrayblit.

import struct

import bitmaptools
import board
import displayio

try:
    from ulab import numpy as np
except ImportError:              # not in every build: recolour in Python, slower
    np = None

import layout
from layout import H, W

DISPLAY = board.DISPLAY
DISPLAY.auto_refresh = False

# ebony, ivory, brass and hammer-felt red (docs/design.md, "The panel")
EBONY, PANEL, LINE = 0x0C0B0A, 0x1E1B18, 0x38322C
IVORY, MUTED, FAINT = 0xF0E8D6, 0x968C7C, 0x5F584E
BRASS, BRASS_DIM, FELT, GREEN, ORANGE = 0xD6AA52, 0x786032, 0xD63C34, 0x6EBE78, 0xE09030
KEY_DONE_W, KEY_DONE_B, KEY_TODO, SELECT_BLUE = 0x46403A, 0x28241F, 0x181614, 0x305080
COLOURS = (EBONY, PANEL, LINE, IVORY, MUTED, FAINT, BRASS, BRASS_DIM, FELT, GREEN, ORANGE,
           KEY_DONE_W, KEY_DONE_B, KEY_TODO, SELECT_BLUE)
INDEX = {c: i for i, c in enumerate(COLOURS)}

PAL = displayio.Palette(16)
for _i, _c in enumerate(COLOURS):
    PAL[_i] = _c
FB = displayio.Bitmap(W, H, 16)
_root = displayio.Group()
_root.append(displayio.TileGrid(FB, pixel_shader=PAL))
DISPLAY.root_group = _root

_dirty = [False]


def refresh(force=False):
    """Push what changed to the display: displayio sends only the changed area."""
    if _dirty[0] or force:
        _dirty[0] = False
        try:
            DISPLAY.refresh()
        except RuntimeError:     # mid-refresh already: the next one catches up
            _dirty[0] = True


# --- rectangles ------------------------------------------------------------------------------
def _clip(r, c):
    """The intersection of two (x0, y0, x1, y1) rectangles, or None."""
    x0, y0 = max(r[0], c[0]), max(r[1], c[1])
    x1, y1 = min(r[2], c[2]), min(r[3], c[3])
    return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None


SCREEN = (0, 0, W, H)


def fill(r, colour, clip=SCREEN):
    r = _clip(r, clip)
    if r:
        bitmaptools.fill_region(FB, r[0], r[1], r[2], r[3], INDEX[colour])
        _dirty[0] = True


# --- fonts -----------------------------------------------------------------------------------
class Font:
    """A .fnt file: its glyph table in RAM (11 bytes a glyph), the glyphs left in flash."""

    def __init__(self, path):
        self.f = open(path, "rb")
        head = self.f.read(8)
        if head[:4] != b"PF1\0":
            raise ValueError("not a panel font: " + path)
        self.ascent, self.descent, self.n = struct.unpack("<BBH", head[4:])
        self.table = self.f.read(11 * self.n)
        self.height = self.ascent + self.descent

    def glyph(self, ch):
        """(w, h, xoff, yoff, advance, offset) for a character, '?' for one the font lacks."""
        cp = ord(ch)
        i = cp - 32                    # most text is printable ASCII, which comes first: try
        if 0 <= i < self.n and struct.unpack_from("<H", self.table, i * 11)[0] == cp:    # straight
            return struct.unpack_from("<BBbbBI", self.table, i * 11 + 2)
        lo, hi = 0, self.n - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            c = struct.unpack_from("<H", self.table, mid * 11)[0]
            if c == cp:
                return struct.unpack_from("<BBbbBI", self.table, mid * 11 + 2)
            if c < cp:
                lo = mid + 1
            else:
                hi = mid - 1
        return self.glyph("?") if ch != "?" else (0, 0, 0, 0, 4, 0)

    def width(self, s):
        return sum(self.glyph(ch)[4] for ch in s)

    def draw(self, s, x, top, colour, clip):
        """s with its left at x and the font's top at `top`, inside clip."""
        base = top + self.ascent
        c = INDEX[colour]
        for ch in s:
            w, h, xo, yo, adv, off = self.glyph(ch)
            if w and h:
                gx, gy = x + xo, base - yo - h
                r = _clip((gx, gy, gx + w, gy + h), clip)
                if r:
                    self._blit(off, w, h, gx, gy, r, c)
            x += adv
        _dirty[0] = True

    def _blit(self, off, w, h, gx, gy, r, c):
        n = w * h
        buf = _glyph_buffer(n)
        self.f.seek(off)
        data = memoryview(buf)[:n]
        self.f.readinto(data)
        # 1 -> this colour's palette index; 0 stays 0 and isn't copied (EBONY is never text)
        if np is not None:
            view = _GLYPH[1][:n]
            view *= c
        else:
            for i in range(n):
                if buf[i]:
                    buf[i] = c
        if r == (gx, gy, gx + w, gy + h):
            bitmaptools.arrayblit(FB, data, gx, gy, gx + w, gy + h, 0)
            return
        cw = r[2] - r[0]               # partly hidden: copy the visible part, row by row
        row = bytearray(cw)
        for y in range(r[1], r[3]):
            start = (y - gy) * w + (r[0] - gx)
            row[:] = buf[start:start + cw]
            bitmaptools.arrayblit(FB, row, r[0], y, r[2], y + 1, 0)


_GLYPH = [bytearray(48 * 36), None]       # one scratch buffer for every font's glyphs


def _glyph_buffer(n):
    if n > len(_GLYPH[0]):
        _GLYPH[0] = bytearray(n)
        _GLYPH[1] = None
    if np is not None and _GLYPH[1] is None:
        _GLYPH[1] = np.frombuffer(_GLYPH[0], dtype=np.uint8)
    return _GLYPH[0]


_fonts = {}


def font(name):
    """title, title_s, big (EB Garamond); body, body_b (Barlow Condensed); label (Silkscreen)."""
    if name not in _fonts:
        _fonts[name] = Font("/fonts/%s.fnt" % name)
    return _fonts[name]


def width(name, s):
    return font(name).width(s)


# --- widgets ---------------------------------------------------------------------------------
class Widget:
    """Something on a screen with a rectangle (x0, y0, x1, y1). It redraws itself within a clip;
    the screen repaints whatever overlaps a changed area, in the order the widgets were added."""

    rect = None
    hidden = False

    def __init__(self, screen):
        self.screen = screen
        screen.items.append(self)

    def show(self, on):
        if self.hidden == on:
            self.hidden = not on
            self.screen.damage(self.rect)


class Text(Widget):
    """A line of text that stays within `room` pixels (ellipsis), anchored like displayio's
    labels: anchor (0, 0) is the top left, (1, 0) the top right, (0.5, 0.5) the centre."""

    def __init__(self, screen, x, y, face="body", color=IVORY, anchor=(0, 0), room=None, text=""):
        super().__init__(screen)
        self.face, self.x, self.y, self.anchor = face, x, y, anchor
        ax = anchor[0]
        self.room = room if room is not None else (W - x - 2 if ax == 0 else (x - 2 if ax == 1 else 2 * min(x, W - x) - 4))
        self.s, self.color = "", color
        self.set(text)

    def _place(self):
        if not self.s:
            return None
        f = font(self.face)
        w = f.width(self.s)
        x0 = int(self.x - self.anchor[0] * w)
        y0 = int(self.y - self.anchor[1] * f.height)
        return (x0, y0, x0 + w, y0 + f.height)

    def set(self, s, color=None):
        s = layout.fit(s, self.room, font(self.face).width) if s else ""
        color = self.color if color is None else color
        if s == self.s and color == self.color:
            return
        old = self.rect
        self.s, self.color = s, color
        self.rect = self._place()
        self.screen.damage(old)
        if self.rect != old:
            self.screen.damage(self.rect)

    def draw(self, clip):
        if self.rect:
            font(self.face).draw(self.s, self.rect[0], self.rect[1], self.color, clip)


class Box(Widget):
    """A filled rectangle whose colour, place and size can change."""

    def __init__(self, screen, x, y, w, h, color):
        super().__init__(screen)
        self.c = color
        self.rect = (x, y, x + w, y + h) if w > 0 and h > 0 else None
        screen.damage(self.rect)

    def place(self, x, y, w, h):
        new = (x, y, x + w, y + h) if w > 0 and h > 0 else None
        if new != self.rect:
            old, self.rect = self.rect, new
            self.screen.damage(old)
            self.screen.damage(new)

    def color(self, c):
        if c != self.c:
            self.c = c
            self.screen.damage(self.rect)

    def draw(self, clip):
        if self.rect:
            fill(self.rect, self.c, clip)


# key states in the strip
K_WHITE, K_BLACK, K_LIT, K_REC, K_DONE_W, K_DONE_B, K_TODO, K_NOW, K_BAD = range(9)
_KEY_COLOURS = (IVORY, EBONY, BRASS, FELT, KEY_DONE_W, KEY_DONE_B, KEY_TODO, BRASS, FELT)


class Keys(Widget):
    """The 88 keys, 156 px wide: lit as the piano plays them, or coloured key by key (the
    calibration map). Nothing overlaps the strip, so a key that changes is painted directly."""

    def __init__(self, screen, y, h):
        super().__init__(screen)
        self.x0, self.y, self.h = (W - layout.KEYS_W) // 2, y, h
        self.rect = (self.x0, y, self.x0 + layout.KEYS_W, y + h)
        self.state = bytearray(K_BLACK if layout.is_black(k) else K_WHITE
                               for k in range(layout.KEY_LO, layout.KEY_HI + 1))
        self.lit_colour = K_LIT
        screen.damage(self.rect)

    def _paint(self, k, clip):
        c = _KEY_COLOURS[self.state[k - layout.KEY_LO]]
        for x, y0, y1 in layout.key_columns(k, self.h):
            fill((self.x0 + x, self.y + y0, self.x0 + x + 1, self.y + y1), c, clip)

    def draw(self, clip):
        for k in range(layout.KEY_LO, layout.KEY_HI + 1):
            self._paint(k, clip)

    def rest(self, k):
        return K_BLACK if layout.is_black(k) else K_WHITE

    def set(self, k, v):
        i = k - layout.KEY_LO
        if 0 <= i < 88 and self.state[i] != v:
            self.state[i] = v
            if self.screen.active() and not self.hidden:
                self._paint(k, SCREEN)

    def press(self, on=(), off=()):
        for k in off:
            self.set(k, self.rest(k))
        for k in on:
            self.set(k, self.lit_colour)


class Lanes(Widget):
    """Performance mode's loop: one lane per layer, the notes in it as cells."""

    L1, L2, L3, REC = IVORY, BRASS, MUTED, FELT

    def __init__(self, screen, x, y, cells, cell_w, lanes=3, lane_h=12):
        super().__init__(screen)
        self.x, self.y, self.cells, self.cw, self.lh, self.n = x, y, cells, cell_w, lane_h, lanes
        self.rect = (x, y, x + cells * cell_w, y + lanes * lane_h)
        self.shown = [[None] * cells for _ in range(lanes)]
        screen.damage(self.rect)

    def _cell(self, n, i, clip):
        x, y = self.x + i * self.cw, self.y + n * self.lh
        want = self.shown[n][i]
        if want is None:
            fill((x, y + 3, x + self.cw, y + 4), LINE, clip)
        else:
            fill((x, y, x + self.cw, y + 7), want, clip)

    def draw(self, clip):
        for n in range(self.n):
            for i in range(self.cells):
                self._cell(n, i, clip)

    def set(self, n, on, colour):
        """Lane n's cells: on[i] True draws a note in `colour`."""
        row = self.shown[n]
        for i in range(self.cells):
            want = colour if on[i] else None
            if row[i] != want:
                row[i] = want
                if self.screen.active():
                    x, y = self.x + i * self.cw, self.y + n * self.lh
                    fill((x, y, x + self.cw, y + 7), EBONY)
                    self._cell(n, i, SCREEN)


class Screen:
    """One screen: an ebony background and its widgets, drawn into the framebuffer while it is
    the one showing. Only one screen shows at a time."""

    showing = None

    def __init__(self):
        self.items = []

    def active(self):
        return Screen.showing is self

    def show(self):
        Screen.showing = self
        self.damage(SCREEN)
        refresh()

    def damage(self, r):
        """Repaint rectangle r: background, then every visible widget over it, in order."""
        if r is None or not self.active():
            return
        r = _clip(r, SCREEN)
        if r is None:
            return
        fill(r, EBONY)
        for w in self.items:
            if not w.hidden and w.rect and _clip(w.rect, r):
                w.draw(r)

    def text(self, *a, **kw):
        return Text(self, *a, **kw)

    def box(self, *a):
        return Box(self, *a)

    def hints(self, items):
        """The bottom row: each button's name in a little key cap, then what it does."""
        Box(self, 0, H - layout.HINT_H, W, layout.HINT_H, PANEL)
        x = 3
        for key, what in items:
            kw = width("label", key) + 4
            Box(self, x, H - 9, kw, 7, LINE)
            Text(self, x + 2, H - 9, "label", IVORY, text=key)
            x += kw + 2
            if what:
                Text(self, x, H - 9, "label", MUTED, text=what)
                x += width("label", what) + 6


def message(title, body="", color=IVORY, face="title"):
    """A whole-screen message (the caller decides how long it stays)."""
    s = Screen()
    y = 40
    for line in layout.wrap(title, W - 16, font(face).width, 2):
        Text(s, W // 2, y, face, color, (0.5, 0.5), text=line)
        y += 18
    y += 4
    for line in layout.wrap(body, W - 16, font("body").width, 4):
        Text(s, W // 2, y, "body", MUTED, (0.5, 0), text=line)
        y += 15
    s.show()
    return s
