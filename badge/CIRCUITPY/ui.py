# The panel's drawing layer: screens built once, widgets changed in place, the display refreshed
# by hand and only when something changed. (The first panel rebuilt the whole screen for every
# change, which made the display blank and redraw several times a second.)

import board
import displayio
import terminalio
import vectorio
from adafruit_display_text import bitmap_label

import layout
from layout import H, W

try:
    import bitmaptools
except ImportError:
    bitmaptools = None

DISPLAY = board.DISPLAY
DISPLAY.auto_refresh = False

# ebony, ivory, brass and hammer-felt red (docs/design.md, "The panel")
EBONY, PANEL, LINE = 0x0C0B0A, 0x1E1B18, 0x38322C
IVORY, MUTED, FAINT = 0xF0E8D6, 0x968C7C, 0x5F584E
BRASS, BRASS_DIM, FELT, GREEN, ORANGE = 0xD6AA52, 0x786032, 0xD63C34, 0x6EBE78, 0xE09030

_dirty = [False]


def dirty():
    _dirty[0] = True


def refresh(force=False):
    """Push what changed to the display: only the changed areas are redrawn."""
    if _dirty[0] or force:
        _dirty[0] = False
        try:
            DISPLAY.refresh()
        except RuntimeError:     # mid-refresh already: the next one catches up
            _dirty[0] = True


# --- fonts -----------------------------------------------------------------------------------
_fonts = {}


def font(name):
    """title, title_s, big (EB Garamond); body, body_b (Barlow Condensed); label (Silkscreen).
    Made by badge/tools/mkfonts.py; the built-in font if one won't load."""
    if name not in _fonts:
        try:
            from adafruit_bitmap_font import bitmap_font
            f = bitmap_font.load_font("/fonts/%s.pcf" % name)
        except (OSError, ValueError, MemoryError, ImportError) as e:
            print("font", name, e)
            f = terminalio.FONT
        _fonts[name] = f
    return _fonts[name]


def width(name, s):
    f, w = font(name), 0
    for ch in s:
        g = f.get_glyph(ord(ch))
        if g is None:
            g = f.get_glyph(ord("?"))
        if g is not None:
            w += g.shift_x
    return w


# --- widgets ---------------------------------------------------------------------------------
class Text:
    """A line of text that stays within `room` pixels (ellipsis), changed only when it differs."""

    def __init__(self, parent, x, y, face="body", color=IVORY, anchor=(0, 0), room=None, text=""):
        self.face, self.room = face, room if room is not None else self._room(x, anchor[0])
        self.lb = bitmap_label.Label(font(face), text=" ", color=color)
        self.lb.anchor_point = anchor
        self.lb.anchored_position = (x, y)
        self.s, self.color = None, color
        parent.append(self.lb)
        self.set(text)

    @staticmethod
    def _room(x, ax):
        return W - x - 2 if ax == 0 else (x - 2 if ax == 1 else 2 * min(x, W - x) - 4)

    def set(self, s, color=None):
        s = layout.fit(s or "", self.room, lambda t: width(self.face, t)) if s else ""
        if s != self.s:
            self.s = s
            self.lb.text = s or " "
            dirty()
        if color is not None and color != self.color:
            self.color = color
            self.lb.color = color
            dirty()

    def show(self, on):
        if self.lb.hidden == on:
            self.lb.hidden = not on
            dirty()


class Box:
    """A filled rectangle whose colour, place and size can change."""

    def __init__(self, parent, x, y, w, h, color):
        self.pal = displayio.Palette(1)
        self.pal[0] = color
        self.geom = None
        self.node = vectorio.Rectangle(pixel_shader=self.pal, width=max(1, w), height=max(1, h), x=x, y=y)
        parent.append(self.node)
        self.place(x, y, w, h)

    def place(self, x, y, w, h):
        if (x, y, w, h) == self.geom:
            return
        self.geom = (x, y, w, h)
        if w <= 0 or h <= 0:
            self.node.hidden = True
        else:
            self.node.hidden = False
            self.node.x, self.node.y = x, y
            self.node.width, self.node.height = w, h
        dirty()

    def color(self, c):
        if self.pal[0] != c:
            self.pal[0] = c
            dirty()

    def show(self, on):
        if self.node.hidden == on:
            self.node.hidden = not on
            dirty()


# key colours in the strip's palette
K_BG, K_WHITE, K_BLACK, K_LIT, K_REC, K_DONE_W, K_DONE_B, K_TODO, K_NOW, K_BAD = range(10)


class Keys:
    """The 88 keys, 156 px wide: lit as the piano plays them, or coloured key by key (the
    calibration map). Only the keys that change are redrawn."""

    def __init__(self, parent, y, h):
        self.h = h
        self.pal = displayio.Palette(10)
        for i, c in enumerate((EBONY, IVORY, EBONY, BRASS, FELT, 0x46403A, 0x28241F, 0x181614, BRASS, FELT)):
            self.pal[i] = c
        self.bmp = displayio.Bitmap(layout.KEYS_W, h, 10)
        self.grid = displayio.TileGrid(self.bmp, pixel_shader=self.pal, x=(W - layout.KEYS_W) // 2, y=y)
        parent.append(self.grid)
        self.cols = {k: layout.key_columns(k, h) for k in range(layout.KEY_LO, layout.KEY_HI + 1)}
        self.state = {}
        self.lit_colour = K_LIT
        self.clear()

    def _paint(self, k, v):
        for x, y0, y1 in self.cols[k]:
            if bitmaptools:
                bitmaptools.fill_region(self.bmp, x, y0, x + 1, y1, v)
            else:
                for y in range(y0, y1):
                    self.bmp[x, y] = v

    def rest(self, k):
        return K_BLACK if layout.is_black(k) else K_WHITE

    def set(self, k, v):
        if k in self.cols and self.state.get(k) != v:
            self.state[k] = v
            self._paint(k, v)          # keys' columns never overlap (layout.key_columns)
            dirty()

    def clear(self):
        for k in self.cols:
            self.set(k, self.rest(k))

    def press(self, on=(), off=()):
        for k in off:
            self.set(k, self.rest(k))
        for k in on:
            self.set(k, self.lit_colour)


class Lanes:
    """Performance mode's loop: one lane per layer, the notes in it as cells. One bitmap; only
    cells that change are repainted."""

    COLOURS = (EBONY, LINE, IVORY, BRASS, MUTED, FELT)
    BG, RULE, L1, L2, L3, REC = range(6)

    def __init__(self, parent, x, y, cells, cell_w, lanes=3, lane_h=12):
        self.cells, self.cw, self.lh, self.n = cells, cell_w, lane_h, lanes
        self.pal = displayio.Palette(len(self.COLOURS))
        for i, c in enumerate(self.COLOURS):
            self.pal[i] = c
        self.bmp = displayio.Bitmap(cells * cell_w, lanes * lane_h, len(self.COLOURS))
        parent.append(displayio.TileGrid(self.bmp, pixel_shader=self.pal, x=x, y=y))
        self.shown = [[None] * cells for _ in range(lanes)]
        for n in range(lanes):
            self._fill(0, n * lane_h + 3, cells * cell_w, 1, self.RULE)

    def _fill(self, x, y, w, h, v):
        if bitmaptools:
            bitmaptools.fill_region(self.bmp, x, y, x + w, y + h, v)
        else:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    self.bmp[xx, yy] = v

    def set(self, n, on, colour):
        """Lane n's cells: on[i] True draws a note in `colour` (one of L1, L2, L3, REC)."""
        row = self.shown[n]
        for i in range(self.cells):
            want = colour if on[i] else None
            if row[i] != want:
                row[i] = want
                x, y = i * self.cw, n * self.lh
                self._fill(x, y, self.cw, 7, self.BG)
                if want is None:
                    self._fill(x, y + 3, self.cw, 1, self.RULE)
                else:
                    self._fill(x, y, self.cw, 7, want)
                dirty()


class Screen:
    """One screen's display tree: an ebony background and whatever is added."""

    def __init__(self):
        self.g = displayio.Group()
        Box(self.g, 0, 0, W, H, EBONY)

    def show(self):
        DISPLAY.root_group = self.g
        dirty()
        refresh()

    def text(self, *a, **kw):
        return Text(self.g, *a, **kw)

    def box(self, *a):
        return Box(self.g, *a)

    def hints(self, items):
        """The bottom row: each button's name in a little key cap, then what it does."""
        Box(self.g, 0, H - layout.HINT_H, W, layout.HINT_H, PANEL)
        x = 3
        for key, what in items:
            kw = width("label", key) + 4
            Box(self.g, x, H - 9, kw, 7, LINE)
            Text(self.g, x + 2, H - 9, "label", IVORY, text=key)
            x += kw + 2
            if what:
                Text(self.g, x, H - 9, "label", MUTED, text=what)
                x += width("label", what) + 6


def message(title, body="", color=IVORY, face="title"):
    """A whole-screen message (the caller decides how long it stays)."""
    s = Screen()
    y = 40
    for line in layout.wrap(title, W - 16, lambda t: width(face, t), 2):
        s.text(W // 2, y, face, color, (0.5, 0.5), text=line)
        y += 18
    y += 4
    for line in layout.wrap(body, W - 16, lambda t: width("body", t), 4):
        s.text(W // 2, y, "body", MUTED, (0.5, 0), text=line)
        y += 15
    s.show()
    return s
