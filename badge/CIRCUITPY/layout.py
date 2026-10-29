# Screen geometry, list maths and text fitting, kept free of CircuitPython imports so the same
# file runs in tests on a laptop (badge/tests/test_layout.py). The badge sits on the piano, where
# a laptop usually isn't.

W, H = 160, 128
MARGIN = 4
HINT_H = 11                     # the button-hint row along the bottom

# --- text ------------------------------------------------------------------------------------
ELLIPSIS = "…"


def fit(s, room, width):
    """s, shortened with an ellipsis if it is wider than `room` pixels. `width(text)` measures
    in the font it will be drawn in."""
    s = str(s)
    if width(s) <= room:
        return s
    while s and width(s + ELLIPSIS) > room:
        s = s[:-1]
    s = s.rstrip(" ,·–-")
    return s + ELLIPSIS if s else ""


def wrap(s, room, width, lines=2):
    """s in at most `lines` lines no wider than `room`; the last one shortened if it must be."""
    words, out, line = str(s).split(), [], ""
    for i, word in enumerate(words):
        trial = (line + " " + word).strip()
        if width(trial) <= room or not line:
            line = trial
            continue
        out.append(line)
        line = word
        if len(out) == lines - 1:
            line = " ".join(words[i:])
            break
    if line:
        out.append(fit(line, room, width))
    return out[:lines]


# --- lists -------------------------------------------------------------------------------------
def list_window(sel, top, rows):
    """The first visible row of a scrolling list, so the selection is always on screen."""
    return min(max(top, sel - rows + 1), sel)


def sections(items, key):
    """[(section, [indexes])] in first-seen order: for jumping a list by era or letter."""
    out, where = [], {}
    for i, it in enumerate(items):
        k = key(it)
        if k not in where:
            where[k] = len(out)
            out.append((k, []))
        out[where[k]][1].append(i)
    return out


def jump(sel, secs, step):
    """The first row of the next (step=1) or previous (step=-1) section, wrapping round."""
    here = next(n for n, (_, idx) in enumerate(secs) if sel in idx)
    return secs[(here + step) % len(secs)][1][0]


ERAS = ("baroque", "classical", "romantic", "modern", None)


def by_era(composers):
    """Composers grouped by era in history's order, most works first within an era."""
    def rank(c):
        e = c.get("era")
        return (ERAS.index(e) if e in ERAS else len(ERAS), -c.get("works", 0), c.get("sort") or c["name"])
    return sorted(composers, key=rank)


# --- numbers -------------------------------------------------------------------------------------
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
    s = max(0, int(seconds or 0))
    return "%d:%02d" % (s // 60, s % 60)


def dates(born, died):
    if born and died:
        return "%d–%d" % (born, died)
    if born:
        return "b. %d" % born
    return ""


# --- the 88 keys -----------------------------------------------------------------------------------
KEY_LO, KEY_HI = 21, 108
BLACK = (1, 3, 6, 8, 10)
KEYS_W = 52 * 3                 # 52 white keys, 2 px each and a 1 px gap


def is_black(k):
    return k % 12 in BLACK


def key_columns(k, h):
    """Where key k is in the strip: [(x, y0, y1)] column runs, y1 exclusive, in a strip KEYS_W
    wide and h high. White keys are 2 px wide; a black key covers the top 60% of the right column
    of the white key below it and the gap after."""
    bh = h * 6 // 10
    white_index = sum(1 for j in range(KEY_LO, k) if not is_black(j))
    if is_black(k):
        x = (white_index - 1) * 3 + 1
        return [(x, 0, bh), (x + 1, 0, bh)]
    x = white_index * 3
    right_top = bh if (k + 1 <= KEY_HI and is_black(k + 1)) else 0
    return [(x, 0, h), (x + 1, right_top, h)]


def lane(notes, cells):
    """Note starts as fractions of the loop -> `cells` on/off cells for a performance lane."""
    out = [False] * cells
    for f in notes:
        out[min(cells - 1, max(0, int(f * cells)))] = True
    return out
