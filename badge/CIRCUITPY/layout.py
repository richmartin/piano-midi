# Screen geometry and list maths, kept free of CircuitPython imports so the same file runs in
# tests on a laptop (badge/tests/test_layout.py). The badge sits on the piano, where a laptop
# usually isn't.

W, H = 160, 128
CHAR_W, CHAR_H = 6, 12          # terminalio.FONT
MARGIN = 4
LIST_ROWS = 7                   # rows a choose() list shows at once


def text_width(s, scale=1):
    return len(s) * CHAR_W * scale


def fit(s, x, scale=1, anchor_x=0.0):
    """s, shortened if needed so that, placed at x with this anchor, it stays on the screen."""
    if anchor_x == 0.0:
        room = W - x
    elif anchor_x == 1.0:
        room = x
    else:                       # centred
        room = 2 * min(x, W - x)
    n = max(0, room // (CHAR_W * scale))
    return s if len(s) <= n else s[:n]


def list_window(sel, top, rows=LIST_ROWS):
    """The first visible row of a scrolling list, so the selection is always on screen."""
    return min(max(top, sel - rows + 1), sel)


def bar_fill(frac, width):
    """Filled pixels of a progress bar: clamped, and never a sliver of 0 when something is
    playing (frac > 0 shows at least one pixel)."""
    if frac <= 0:
        return 0
    if frac >= 1:
        return width
    return max(1, int(frac * width))


def mmss(seconds):
    """83.4 -> '1:23'. The badge shows whole seconds."""
    s = max(0, int(seconds))
    return "%d:%02d" % (s // 60, s % 60)
