"""The badge's layout maths, run on a laptop — the badge sits on the piano, where pytest doesn't.

Don't put the CIRCUITPY folder first on sys.path: its code.py would shadow the standard
library's `code` module, which pytest's debugger imports. Import layout by file instead.
"""

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("badge_layout", os.path.join(HERE, "..", "CIRCUITPY", "layout.py"))
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


class TestFit:
    def test_short_text_untouched(self):
        assert layout.fit("hello", 4) == "hello"

    def test_left_anchored_clips_to_the_right_edge(self):
        s = "x" * 50
        fitted = layout.fit(s, 10)
        assert layout.text_width(fitted) <= layout.W - 10
        assert fitted == s[:25]

    def test_right_anchored_clips_to_the_left_edge(self):
        fitted = layout.fit("x" * 50, 60, anchor_x=1.0)
        assert layout.text_width(fitted) <= 60

    def test_centred_clips_both_ways(self):
        fitted = layout.fit("x" * 50, layout.W // 2, anchor_x=0.5)
        assert layout.text_width(fitted) <= layout.W

    def test_scale_counts(self):
        fitted = layout.fit("x" * 50, 0, scale=2)
        assert layout.text_width(fitted, 2) <= layout.W

    def test_no_room_means_empty(self):
        assert layout.fit("hello", layout.W) == ""


class TestListWindow:
    def test_selection_below_the_window_scrolls_down(self):
        assert layout.list_window(sel=10, top=0, rows=7) == 4

    def test_selection_above_the_window_scrolls_up(self):
        assert layout.list_window(sel=2, top=5, rows=7) == 2

    def test_selection_inside_stays_put(self):
        assert layout.list_window(sel=5, top=3, rows=7) == 3

    def test_wrap_to_top(self):
        # 600 works, selection wraps from the last to the first
        assert layout.list_window(sel=0, top=593, rows=7) == 0


class TestBar:
    def test_empty_full_and_clamped(self):
        assert layout.bar_fill(0, 100) == 0
        assert layout.bar_fill(1, 100) == 100
        assert layout.bar_fill(2.5, 100) == 100
        assert layout.bar_fill(-1, 100) == 0

    def test_something_playing_always_shows(self):
        assert layout.bar_fill(0.001, 100) == 1

    def test_middle(self):
        assert layout.bar_fill(0.5, 100) == 50


class TestMmss:
    def test_formats(self):
        assert layout.mmss(0) == "0:00"
        assert layout.mmss(83.4) == "1:23"
        assert layout.mmss(600) == "10:00"
        assert layout.mmss(-3) == "0:00"


def test_hints_fit_the_screen():
    """Every hint and one-line text fits in 26 characters, in the small font."""
    spec2 = importlib.util.spec_from_file_location("badge_texts", os.path.join(HERE, "..", "CIRCUITPY", "texts.py"))
    texts = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(texts)
    for key, value in texts.T.items():
        if isinstance(value, str) and (key.startswith("hint") or key.startswith("p_") or key == "no_piano"):
            assert len(value) <= 26, f"{key} is {len(value)} chars: {value!r}"
