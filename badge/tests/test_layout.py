"""The badge's layout maths, run on a laptop — the badge sits on the piano, where pytest doesn't.

Don't put the CIRCUITPY folder first on sys.path: its code.py would shadow the standard
library's `code` module, which pytest's debugger imports. Import layout by file instead.
"""

import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location("badge_" + name, os.path.join(HERE, "..", "CIRCUITPY", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


layout = load("layout")
FONTS = json.load(open(os.path.join(HERE, "font_widths.json"), encoding="utf-8"))


def width(face):
    adv = FONTS[face]["advance"]
    return lambda s: sum(adv.get(ch, adv["?"]) for ch in s)


class TestFit:
    def test_short_text_untouched(self):
        assert layout.fit("Mozart", 100, width("body")) == "Mozart"

    def test_long_text_gets_an_ellipsis_and_fits(self):
        w = width("body")
        s = layout.fit("Variations on Ah vous dirai-je, Maman", 100, w)
        assert s.endswith("…") and w(s) <= 100

    def test_no_dangling_punctuation_before_the_ellipsis(self):
        s = layout.fit("Mozart, played by Wanda Landowska", 60, width("body"))
        assert not s[:-1].endswith((" ", ","))

    def test_no_room_means_empty(self):
        assert layout.fit("hello", 0, width("body")) == ""


class TestWrap:
    def test_two_lines_and_the_rest_shortened(self):
        w = width("title")
        lines = layout.wrap("Variations on a theme by Corelli, Op. 42, with the long coda", 150, w, 2)
        assert len(lines) == 2 and all(w(l) <= 150 for l in lines)
        assert lines[-1].endswith("…")

    def test_short_stays_one_line(self):
        assert layout.wrap("Nocturne", 150, width("title"), 2) == ["Nocturne"]


class TestListsAndSections:
    def test_window(self):
        assert layout.list_window(sel=10, top=0, rows=7) == 4
        assert layout.list_window(sel=2, top=5, rows=7) == 2
        assert layout.list_window(sel=5, top=3, rows=7) == 3

    def test_eras_in_order_most_works_first(self):
        cs = [{"name": "Field", "era": "classical", "works": 1}, {"name": "Chopin", "era": "romantic", "works": 69},
              {"name": "Mozart", "era": "classical", "works": 25}, {"name": "Bach", "era": "baroque", "works": 9},
              {"name": "Beethoven", "era": "classical", "works": 38}, {"name": "?", "era": None, "works": 3}]
        assert [c["name"] for c in layout.by_era(cs)] == ["Bach", "Beethoven", "Mozart", "Field", "Chopin", "?"]

    def test_jumping_sections_wraps(self):
        cs = layout.by_era([{"name": n, "era": e, "works": 1} for n, e in
                            (("Bach", "baroque"), ("Mozart", "classical"), ("Haydn", "classical"), ("Liszt", "romantic"))])
        secs = layout.sections(cs, lambda c: c["era"])
        assert [s for s, _ in secs] == ["baroque", "classical", "romantic"]
        assert layout.jump(0, secs, 1) == 1          # Bach -> the first classical
        assert layout.jump(2, secs, 1) == 3          # Haydn -> Liszt
        assert layout.jump(3, secs, 1) == 0          # Liszt -> round to Bach
        assert layout.jump(0, secs, -1) == 3


class TestKeys:
    def test_88_keys_fill_the_strip_without_overlapping(self):
        h = 18
        seen = {}
        for k in range(layout.KEY_LO, layout.KEY_HI + 1):
            for x, y0, y1 in layout.key_columns(k, h):
                assert 0 <= x < layout.KEYS_W and 0 <= y0 < y1 <= h
                for y in range(y0, y1):
                    assert (x, y) not in seen, (k, x, y, seen.get((x, y)))
                    seen[(x, y)] = k
        assert sum(1 for k in range(21, 109) if not layout.is_black(k)) == 52

    def test_a_black_key_sits_between_its_white_neighbours(self):
        (bx, _, _), _ = layout.key_columns(61, 18)       # C#4
        (cx, _, _), _ = layout.key_columns(60, 18)       # C4
        (dx, _, _), _ = layout.key_columns(62, 18)       # D4
        assert cx < bx < dx


class TestNumbers:
    def test_bar(self):
        assert layout.bar_fill(0, 100) == 0 and layout.bar_fill(2.5, 100) == 100
        assert layout.bar_fill(0.001, 100) == 1 and layout.bar_fill(0.5, 100) == 50

    def test_mmss_and_dates(self):
        assert layout.mmss(83.4) == "1:23" and layout.mmss(None) == "0:00"
        assert layout.dates(1756, 1791) == "1756–1791" and layout.dates(None, None) == ""

    def test_lane(self):
        assert layout.lane([0.0, 0.5, 0.999], 4) == [True, False, True, True]


def test_every_short_text_fits_its_place():
    """Hints are drawn in the 8 px label font across the bottom; nothing may run off."""
    texts = load("texts")
    w = width("label")
    for key, value in texts.T.items():
        if isinstance(value, str) and key.startswith("h_"):
            assert w(value) <= 60, f"{key} is {w(value)} px: {value!r}"


def test_every_character_the_texts_use_is_in_the_fonts():
    texts = load("texts")
    chars = {ch for v in texts.T.values() for s in (v if isinstance(v, tuple) else (v,)) for ch in s}
    for face in ("body", "body_b", "title", "label"):
        missing = {ch for ch in chars if ch not in FONTS[face]["advance"]} - {"{", "}"}
        if face == "label":
            missing -= {ch for ch in chars if ch.islower()}     # Silkscreen: capitals only
        assert not missing, (face, missing)
