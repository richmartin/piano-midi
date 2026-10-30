# Piano control panel: Adafruit PyBadge LC on the Pi's USB port, on the piano's music desk.
#
# The Pi (pianod) is the source of truth. This shows what pianod pushes, and sends it requests:
# newline-delimited JSON over CircuitPython's second USB serial port (boot.py enables it).
# The design: docs/design.md, "The panel".
#
# One meaning per button, everywhere, so the enclosure can be labelled:
#   A       yes: play, choose, pause (held: star the piece as a favourite)
#           Performance: record, close the loop, overdub
#   B       back (on Now Playing: stop; Performance: end the take, stop)
#   START   Now Playing, from anywhere, and back (Performance: save the take)
#   SELECT  Settings, from anywhere, and back
#   d-pad   lists: up/down move, left/right jump a section
#           Now Playing: up/down volume, left/right previous/next
#           Performance: up undo, down redo
# (The keyboard, for a take's name or a WiFi password, is the one exception: A types, B deletes.)
# The screen dims after a minute; the first press only wakes it.

import gc
import json
import time
import traceback

import board
import keypad
import usb_cdc

import layout
import ui
from layout import (C_BORN, C_DIED, C_ERA, C_FIRST, C_SHORT, C_SLUG, C_WORKS, H, P_BORN, P_DIED,
                    P_NAME, P_SHORT, P_SLUG, P_WORKS, W, W_COMPOSER, W_DURATION, W_ID, W_MOVEMENT,
                    W_PERFORMER, W_TITLE)
from texts import t
from ui import BRASS, EBONY, FAINT, FELT, GREEN, IVORY, LINE, MUTED, ORANGE, PANEL

# --- hardware ---------------------------------------------------------------------------
KEYS = keypad.ShiftRegisterKeys(clock=board.BUTTON_CLOCK, data=board.BUTTON_OUT,
                                latch=board.BUTTON_LATCH, key_count=8, value_when_pressed=True)
B, A, START, SELECT, RIGHT, DOWN, UP, LEFT = range(8)
A_HELD = 100                     # A held down for a while: star
HOLD_S = 0.6
LINK = usb_cdc.data
IDLE_DIM_S = 60


# --- link to pianod ----------------------------------------------------------------------
class Link:
    """Requests out, replies and pushes in. Never blocks the UI for long."""

    def __init__(self):
        self.buf = b""
        self.next_id = 1
        self.replies = {}
        self.state = None
        self.last_heard = -1000.0
        self.keys = None             # the Keys strip on screen, if any: live key pushes go there

    def send(self, cmd, **kw):
        rid = self.next_id
        self.next_id += 1
        kw["cmd"], kw["rid"] = cmd, rid      # "rid": "id" is a work's id (fav)
        if LINK is not None:
            try:
                LINK.write((json.dumps(kw) + "\n").encode())
            except OSError:
                pass
        return rid

    def poll(self):
        if LINK is None:
            return
        n = LINK.in_waiting
        if n:
            self.buf += LINK.read(n)
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            self.last_heard = time.monotonic()
            kind = msg.get("type")
            if kind == "state":
                self.state = msg
            elif kind == "keys":
                if self.keys is not None:
                    self.keys.press(msg.get("on", ()), msg.get("off", ()))
            elif "rid" in msg:
                self.replies[msg["rid"]] = msg
        if len(self.buf) > 4096:    # garbage: start afresh
            self.buf = b""

    def connected(self):
        return time.monotonic() - self.last_heard < 15

    def st(self):
        return self.state if self.connected() else None

    def request(self, cmd, timeout=8.0, **kw):
        """Send and wait for the reply (keeps polling, so pushes still land). None on timeout."""
        rid = self.send(cmd, **kw)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.poll()
            ui.refresh()
            if rid in self.replies:
                return self.replies.pop(rid)
            time.sleep(0.01)
        return None


link = Link()


def fetch(cmd, key, **kw):
    """A whole list, a page at a time: pianod keeps each reply under the 4 KB a line may be, and
    sends compact rows (layout.C_*, P_*, W_*), not dicts: RAM is tight.
    (items, None), or (None, the failed reply)."""
    gc.collect()
    kw["compact"] = 1
    items, offset = [], 0
    while offset is not None:
        r = link.request(cmd, offset=offset, **kw)
        if not r or not r.get("ok"):
            return None, r
        items += r[key]
        offset = r.get("next")
    return items, None


# --- input ------------------------------------------------------------------------------
class Input:
    def __init__(self):
        self.last = time.monotonic()
        self.dimmed = False
        self.held = None
        self.next_rep = 0
        self.a_down = None           # when A went down, until it's released or counts as held

    def key(self):
        """The next key, or None. d-pad keys repeat while held; A counts on release (or as
        A_HELD once held for HOLD_S), so a long press can mean something else."""
        ev = KEYS.events.get()
        now = time.monotonic()
        if ev and ev.pressed:
            self.last = now
            if self.dimmed:
                ui.DISPLAY.brightness = 1.0
                self.dimmed = False
                self.held = None
                return None          # the first press only wakes the screen
            if ev.key_number == A:
                self.a_down = now
                return None
            self.held, self.next_rep = ev.key_number, now + 0.45
            return ev.key_number
        if ev and ev.released:
            if ev.key_number == A and self.a_down is not None:
                self.a_down = None
                return A
            self.held = None
            return None
        if self.a_down is not None and now - self.a_down >= HOLD_S:
            self.a_down = None
            return A_HELD
        if self.held in (UP, DOWN, LEFT, RIGHT) and now >= self.next_rep:
            self.next_rep = now + 0.08
            self.last = now
            return self.held
        if not self.dimmed and now - self.last > IDLE_DIM_S:
            ui.DISPLAY.brightness = 0.08
            self.dimmed = True
        return None


inp = Input()


class GoHome(Exception):
    """Leave every screen at once, back to Play Something."""


class ShowNowPlaying(GoHome):
    """Leave every screen, then open Now Playing: the screens unwind first, so a long list they
    held is freed before Now Playing needs the RAM (it didn't fit beside a collection's list)."""


def tick():
    link.poll()
    ui.refresh()


def next_key(screen=None, idle=None):
    """Wait for a key, keeping the link and the display going. START and SELECT open Now
    Playing and Settings over whatever is showing, which comes back after; `idle(st)` runs
    while waiting (the screen keeps itself up to date)."""
    shown = 0
    while True:
        tick()
        if idle is not None and time.monotonic() - shown > 0.1:     # ten times a second is plenty
            shown = time.monotonic()
            idle(link.st())
        k = inp.key()
        if k is None:
            time.sleep(0.01)
            continue
        if k in (START, SELECT) and screen is not None:
            guarded(now_playing if k == START else settings)
            screen.show()
            link.keys = getattr(screen, "keys", None)
            continue
        return k


def pause(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        tick()
        time.sleep(0.02)


def say(title, body="", color=IVORY, seconds=1.5):
    ui.message(title, body, color)
    pause(seconds)


def log_error(e):
    """Send a traceback to pianod, which logs it with its own (and on to murdoch)."""
    try:
        link.send("log", text="".join(traceback.format_exception(e))[-1500:])
    except Exception:        # noqa: BLE001 - logging must never make it worse
        pass


def report(r, ok_key=None):
    if r is None:
        say(t("no_answer"), t("no_answer_body"), ORANGE, 2.5)
    elif not r.get("ok"):
        say(t("not_done"), r.get("error", ""), FELT, 2.5)
    elif ok_key:
        say(t(ok_key), "", GREEN, 0.8)
    return r is not None and r.get("ok")


# --- lists ---------------------------------------------------------------------------------
ROW_H = 15


class ListView(ui.Screen):
    """A scrolling list whose rows are built once and only relabelled as it moves: the chosen
    row large in Garamond, the others in Barlow, a right-hand column for counts and times.

    rows: [(left, right, right_colour)]; `section(i)` names row i's section (era, letter) for
    LEFT/RIGHT jumps and the header."""

    def __init__(self, title, rows, sel=0, y0=16, section=None, hints=None, head=None):
        super().__init__()
        self.rows, self.sel, self.top, self.y0 = rows, min(sel, max(0, len(rows) - 1)), 0, y0
        self.section = section
        self.n = (H - layout.HINT_H - y0) // ROW_H
        if head is not None:
            head(self)
        else:
            self.title = self.text(4, 4, "label", BRASS, text=title)
            self.side = self.text(W - 4, 4, "label", MUTED, (1, 0), room=70)
        self.bar = self.box(0, 0, W, ROW_H + 2, PANEL)
        self.mark = self.box(0, 0, 2, ROW_H + 2, BRASS)
        self.left, self.right = [], []
        for i in range(self.n):
            y = y0 + i * ROW_H
            self.left.append(self.text(7, y, "body_b", MUTED, room=W - 52))
            self.right.append(self.text(W - 4, y + 1, "body", FAINT, (1, 0), room=48))
        self.secs = layout.sections(range(len(rows)), section) if section and rows else None
        self.hints(hints or [("A", t("h_open")), ("B", t("h_back"))])
        self.draw()

    def draw(self):
        self.top = layout.list_window(self.sel, self.top, self.n)
        for i in range(self.n):
            j = self.top + i
            if j >= len(self.rows):
                self.left[i].set("")
                self.right[i].set("")
                continue
            left, right, rc = (self.rows[j] + (None, None))[:3]
            chosen = j == self.sel
            self.left[i].set(left, IVORY if chosen else MUTED)
            self.right[i].set(right or "", rc or (MUTED if chosen else FAINT))
            if chosen:
                y = self.y0 + i * ROW_H - 1
                self.bar.place(0, y, W, ROW_H + 1)
                self.mark.place(0, y, 2, ROW_H + 1)
        if not self.rows:
            self.bar.show(False)
            self.mark.show(False)
        if self.secs and self.section:
            name = self.section(self.sel)
            if hasattr(self, "side"):
                self.side.set(name)

    def run(self, keys=()):
        """Until a choice: (key, index). B gives (B, None). `keys` are extra keys to return."""
        self.show()
        link.keys = None
        while True:
            k = next_key(self)
            if k == B:
                return B, None
            if not self.rows:
                continue
            if k in (UP, DOWN):
                self.sel = (self.sel + (1 if k == DOWN else -1)) % len(self.rows)
                self.draw()
            elif k in (LEFT, RIGHT) and self.secs:
                self.sel = layout.jump(self.sel, self.secs, 1 if k == RIGHT else -1)
                self.draw()
            elif k == A or k in keys:
                return k, self.sel


def choose(title, options, sel=0, hint=None):
    """A simple list of words: the index chosen, or None for B."""
    k, i = ListView(title, [(o,) for o in options], sel, hints=[("A", hint or t("h_choose")), ("B", t("h_back"))]).run()
    return i


# --- home: play something -------------------------------------------------------------------
def home():
    sel = 0
    while True:
        st = link.st()
        if st and calibrating(st):
            guarded(calibration)
            continue
        if st and st.get("mode") == "performance":
            guarded(performance)          # put there by the CLI or the web: join in
            continue
        sh = link.request("shelves", timeout=4) or {}
        rows = [(t("s_surprise"), None), (t("s_composers"), None), (t("s_pianists"), None),
                (t("s_collections"), None), (t("s_favourites"), None), (t("s_takes"), None),
                (t("s_perform"), None)]
        notes = [t("n_surprise"), t("n_composers", n=sh.get("composers", "")),
                 t("n_pianists", n=sh.get("pianists", "")), t("n_collections"),
                 t("n_favourites", n=sh.get("favourites", 0)), t("n_takes", n=sh.get("takes", 0)),
                 t("n_perform")]
        view = ListView(t("home"), rows, sel, hints=[("A", t("h_open")), ("ST", t("h_now"))])
        view.side.set(notes[sel])

        def follow(i, view=view):
            view.side.set(notes[i])
        k, i = run_home(view, follow)
        if k != A:
            continue
        sel = i
        guarded((surprise, composers, pianists, collections, favourites, takes, performance)[i])


def run_home(view, follow):
    """Home's list, which never goes back anywhere, and comes back to itself every 30 s to see
    whether a calibration or Performance mode started elsewhere."""
    view.show()
    link.keys = None
    since = time.monotonic()
    while True:
        tick()
        st = link.st()
        if st and (calibrating(st) or st.get("mode") == "performance"):
            return None, None
        k = inp.key()
        if k is None:
            if time.monotonic() - since > 30:
                return None, None
            time.sleep(0.01)
            continue
        since = time.monotonic()
        if k in (START, SELECT):
            guarded(now_playing if k == START else settings)
            view.show()
        elif k in (UP, DOWN):
            view.sel = (view.sel + (1 if k == DOWN else -1)) % len(view.rows)
            view.draw()
            follow(view.sel)
        elif k == A:
            return A, view.sel


def surprise():
    if report(link.request("play", all=True, shuffle=True)):
        now_playing()


def play(**what):
    """Play, then show it (once the screens that asked have let go of their lists)."""
    if report(link.request("play", **what)):
        raise ShowNowPlaying()


def star(work_id):
    r = link.request("fav", id=work_id)
    if r and r.get("ok"):
        say(t("starred") if r.get("fav") else t("unstarred"), "", BRASS, 0.9)
    else:
        report(r)


ERA_NAMES = {"baroque": "era_baroque", "classical": "era_classical", "romantic": "era_romantic",
             "modern": "era_modern"}


def composers():
    say(t("loading"), "", MUTED, 0)
    comps, failed = fetch("composers", "composers")
    if comps is None:
        report(failed)
        return
    comps = layout.by_era(comps)
    eras = [c[C_ERA] for c in comps]
    rows = [(c[C_SHORT], str(c[C_WORKS] or "")) for c in comps]
    view = ListView("", rows, 0, section=lambda i: t(ERA_NAMES.get(eras[i], "era_none")),
                    hints=[("<>", t("h_era")), ("A", t("h_open")), ("B", t("h_back"))])
    view.title.set(t("s_composers").upper())
    view.draw()
    while True:
        k, i = view.run()
        if k == B:
            return
        composer(comps[i])


def work_rows(works, right):
    return [(w[W_TITLE] + (" · " + w[W_MOVEMENT] if w[W_MOVEMENT] else ""),) + right(w) for w in works]


def composer(c):
    say(t("loading"), "", MUTED, 0)
    works, failed = fetch("works", "works", slug=c[C_SLUG])
    if works is None:
        report(failed)
        return

    def head(view):
        view.text(4, 2, "body", MUTED, room=96, text=c[C_FIRST] or "")
        view.text(4, 12, "big", IVORY, room=100, text=c[C_SHORT])
        view.text(W - 4, 4, "body", FAINT, (1, 0), room=60, text=layout.dates(c[C_BORN], c[C_DIED]))
        view.text(W - 4, 20, "body", BRASS, (1, 0), room=60, text=t("works_n", n=len(works)))
        view.box(4, 41, W - 8, 1, LINE)

    rows = [(t("play_all"), None, BRASS), (t("shuffle"), None, BRASS)] + work_rows(
        works, lambda w: ((w[W_PERFORMER], BRASS) if w[W_PERFORMER] else (layout.mmss(w[W_DURATION]), None)))
    view = ListView("", rows, 2 if works else 0, y0=44, head=head,
                    hints=[("A", t("h_play")), ("A+", t("h_star")), ("B", t("h_back"))])
    while True:
        k, i = view.run(keys=(A_HELD,))
        if k == B:
            return
        if i < 2:
            play(slug=c[C_SLUG], shuffle=(i == 1))
        elif k == A_HELD:
            star(works[i - 2][W_ID])
            view.show()
        else:
            play(ids=[works[i - 2][W_ID]])


def pianists():
    say(t("loading"), "", MUTED, 0)
    ps, failed = fetch("pianists", "pianists")
    if ps is None:
        report(failed)
        return
    rows = [(p[P_SHORT] or p[P_NAME], str(p[P_WORKS] or "")) for p in ps]
    view = ListView(t("pianists_title"), rows, 0)
    while True:
        k, i = view.run()
        if k == B:
            return
        pianist(ps[i])


def pianist(p):
    say(t("loading"), "", MUTED, 0)
    works, failed = fetch("pianist_works", "works", slug=p[P_SLUG])
    if works is None:
        report(failed)
        return

    def head(view):
        first, _, last = p[P_NAME].rpartition(" ")
        view.text(4, 2, "body", MUTED, room=96, text=first)
        view.text(4, 12, "big", IVORY, room=110, text=p[P_SHORT] or last)
        view.text(W - 4, 4, "body", FAINT, (1, 0), room=60, text=layout.dates(p[P_BORN], p[P_DIED]))
        view.text(W - 4, 20, "body", BRASS, (1, 0), room=60, text=t("rolls_n", n=len(works)))
        view.box(4, 41, W - 8, 1, LINE)

    rows = [(t("play_all"), None, BRASS), (t("shuffle"), None, BRASS)] + work_rows(
        works, lambda w: (w[W_COMPOSER] or "", None))
    view = ListView("", rows, 2 if works else 0, y0=44, head=head,
                    hints=[("A", t("h_play")), ("A+", t("h_star")), ("B", t("h_back"))])
    while True:
        k, i = view.run(keys=(A_HELD,))
        if k == B:
            return
        if i < 2:
            play(pianist=p[P_SLUG], shuffle=(i == 1))
        elif k == A_HELD:
            star(works[i - 2][W_ID])
            view.show()
        else:
            play(ids=[works[i - 2][W_ID]])


def collections():
    sh = link.request("shelves", timeout=4) or {}
    cols = sh.get("collections") or []
    view = ListView(t("collections_title"), [(c["name"], str(c.get("count", ""))) for c in cols], 0)
    while True:
        k, i = view.run()
        if k == B:
            return
        work_list("collection", cols[i]["name"].upper(), slug=cols[i]["slug"])


def favourites():
    work_list("favourites", t("favourites_title"), empty=("no_favourites", "no_favourites_body"))


def takes():
    work_list("recordings", t("takes_title"), empty=("no_takes", "no_takes_body"))


def work_list(cmd, title, empty=None, **kw):
    """A list of works from anywhere (a collection, favourites, takes): play all, shuffle, each."""
    say(t("loading"), "", MUTED, 0)
    works, failed = fetch(cmd, "works", **kw)
    if works is None:
        report(failed)
        return
    if not works and empty:
        say(t(empty[0]), t(empty[1]), MUTED, 2.5)
        return
    ids = [w[W_ID] for w in works]
    rows = [(t("play_all"), None, BRASS), (t("shuffle"), None, BRASS)] + work_rows(
        works, lambda w: (w[W_COMPOSER] or layout.mmss(w[W_DURATION]), None))
    view = ListView(title, rows, 0, hints=[("A", t("h_play")), ("A+", t("h_star")), ("B", t("h_back"))])
    view.side.set(t("works_n", n=len(works)).upper())
    while True:
        k, i = view.run(keys=(A_HELD,))
        if k == B:
            return
        if i < 2:
            play(ids=ids, shuffle=(i == 1))
        elif k == A_HELD:
            star(works[i - 2][W_ID])
            view.show()
        else:
            play(ids=[works[i - 2][W_ID]])


# --- now playing ----------------------------------------------------------------------------
class NowPlaying(ui.Screen):
    def __init__(self):
        super().__init__()
        self.kind = self.text(4, 4, "label", BRASS, room=110)
        self.fav = self.text(W - 26, 4, "label", BRASS, (1, 0), room=60)
        self.vol = [self.box(W - 22 + i * 2, 10 - i, 1, 1 + i, LINE) for i in range(8)]
        self.title = self.text(4, 15, "title", IVORY)
        self.title2 = self.text(4, 32, "title_s", MUTED)
        self.composer = self.text(4, 50, "body_b", BRASS)
        self.performer = self.text(4, 64, "body", IVORY)
        self.track = self.box(4, 82, W - 8, 2, LINE)
        self.done = self.box(4, 82, 0, 2, BRASS)
        self.pos = self.text(4, 87, "label", MUTED)
        self.next = self.text(W // 2, 87, "label", FAINT, (0.5, 0), room=80)
        self.len = self.text(W - 4, 87, "label", MUTED, (1, 0))
        self.keys = ui.Keys(self, 98, 17)
        self.hints([("A", t("h_pause")), ("B", t("h_stop")), ("<>", t("h_skip")), ("A+", t("h_star"))])
        self.warn = self.box(0, H - layout.HINT_H, W, layout.HINT_H, FELT)       # over the hints
        self.warn_text = self.text(W // 2, H - 9, "label", IVORY, (0.5, 0), text=t("no_piano"))
        # the volume card, over everything, shown for a moment after a change
        self.card = self.box(20, 30, W - 40, 50, PANEL)
        self.card_label = self.text(W // 2, 35, "label", MUTED, (0.5, 0), text=t("volume"))
        self.card_bars = [self.box(34 + i * 9, 72 - (6 + i * 2), 6, 6 + i * 2, LINE) for i in range(11)]
        self.card_until = 0
        self.show_card(False)

    def show_card(self, on):
        self.card.show(on)
        self.card_label.show(on)
        for b in self.card_bars:
            b.show(on)

    def update(self, st):
        if st is None:
            self.kind.set(t("no_contact").upper(), ORANGE)
            return
        playing, title = st.get("playing"), st.get("title")
        roll = st.get("roll")
        self.kind.set(t("np_paused") if title and not playing else
                      (t("np_roll", s=roll.upper()) if roll else t("np_file")),
                      MUTED if title and not playing else BRASS)
        self.fav.set(t("np_fav") if st.get("fav") else "")
        v = st.get("volume") or 0
        for i, b in enumerate(self.vol):
            b.color(IVORY if i < round(v * 8 / 11) else LINE)
        if not title:
            self.title.set(t("np_nothing"))
            self.title2.set("")
            self.composer.set("")
            self.performer.set(t("np_nothing_body"), MUTED)
        else:
            lines = layout.wrap(title, W - 8, lambda s: ui.width("title", s), 2)
            mv = st.get("movement")
            self.title.set(lines[0])
            self.title2.set(mv if mv else (lines[1] if len(lines) > 1 else ""))
            self.composer.set(st.get("composer") or "")
            self.performer.set(t("np_played_by", s=st["performer"]) if st.get("performer") else "", IVORY)
        n = st.get("len") or 0
        self.done.place(4, 82, layout.bar_fill((st.get("pos") or 0) / n if n else 0, W - 8), 2)
        self.pos.set(layout.mmss(st.get("pos")) if title else "")
        self.len.set(layout.mmss(n) if title else "")
        self.next.set(t("np_next", s=st["next_title"]).upper() if st.get("next_title") else "")
        missing = not st.get("midi_out")
        self.warn.show(missing)
        self.warn_text.show(missing)
        for i, b in enumerate(self.card_bars):
            b.color(BRASS if i < v else LINE)
        if self.card_until and time.monotonic() > self.card_until:
            self.card_until = 0
            self.show_card(False)


def now_playing():
    view = NowPlaying()
    view.update(link.st())
    view.show()
    link.keys = view.keys
    try:
        while True:
            k = next_key(None, view.update)
            st = link.st() or {}
            if k == START:
                return
            if k == SELECT:
                guarded(settings)
                view.show()
            elif k == A:
                report_quietly(link.request("toggle"))
            elif k == A_HELD and st.get("id"):
                star(st["id"])
                view.show()
            elif k == B:
                report_quietly(link.request("stop"))
                return
            elif k in (LEFT, RIGHT):
                report_quietly(link.request("next" if k == RIGHT else "prev"))
            elif k in (UP, DOWN):
                v = (st.get("volume") or 8) + (1 if k == UP else -1)
                if 1 <= v <= 11:
                    report_quietly(link.request("volume", v=v))
                view.show_card(True)
                view.card_until = time.monotonic() + 1.5
    finally:
        link.keys = None


def report_quietly(r):
    """Actions on screens that show their own result: only failures are worth a message."""
    if r is not None and not r.get("ok"):
        say(t("not_done"), r.get("error", ""), FELT, 2.0)


# --- performance mode: the loop station -----------------------------------------------------
LANE_CELLS = 72


class Perf(ui.Screen):
    def __init__(self):
        super().__init__()
        self.text(4, 4, "label", MUTED, text=t("perf"))
        self.state = self.text(W - 4, 4, "label", GREEN, (1, 0), room=90)
        self.dot = self.box(0, 5, 5, 5, FELT)
        x0, x1 = 14, W - 4
        self.cell = (x1 - x0) // LANE_CELLS
        self.x0 = x0
        for n in range(3):
            self.text(4, 19 + n * 12, "label", FAINT, text=str(n + 1))
        self.lanes = ui.Lanes(self, x0, 18, LANE_CELLS, self.cell)
        self.head = self.box(x0, 16, 1, 37, IVORY)
        self.big = self.text(4, 57, "title", IVORY)
        self.side = self.text(W - 4, 60, "body", MUTED, (1, 0), room=70)
        self.keys = ui.Keys(self, 82, 18)
        self.hints([("A", ""), ("B", ""), ("^v", t("h_undo")), ("ST", t("h_save"))])
        self.a_hint = self.text(3 + ui.width("label", "A") + 6, H - 9, "label", MUTED)
        self.b_hint = self.text(60, H - 9, "label", MUTED)

    def update(self, st):
        p = (st or {}).get("perf") or {}
        s = p.get("state", "empty")
        overdub = p.get("overdub")
        rec = s == "recording" or overdub
        word, color = {"recording": (t("p_rec"), FELT), "stopped": (t("p_stopped"), ORANGE),
                       "playing": (t("p_overdub") if overdub else t("p_looping"), FELT if overdub else GREEN)
                       }.get(s, ("", MUTED))
        self.state.set(word, color)
        self.dot.place(W - 4 - ui.width("label", word) - 8, 5, 5 if rec else 0, 5)
        layers = p.get("layers") or 0
        lanes = p.get("lanes") or []
        shown = lanes[-3:]
        for n in range(3):
            notes = shown[n] if n < len(shown) else []
            top = n == len(shown) - 1
            c = ui.Lanes.REC if (top and rec) else (ui.Lanes.L1, ui.Lanes.L2, ui.Lanes.L3)[n]
            self.lanes.set(n, layout.lane(notes, LANE_CELLS), c)
        length = p.get("length") or 0
        self.head.show(bool(length))
        if length:
            self.head.place(self.x0 + int((p.get("pos", 0) / length) * self.cell * LANE_CELLS), 16, 1, 37)
        if s == "empty":
            self.big.set(t("p_ready"), IVORY)
            self.side.set(t("p_ready_body"))
        else:
            self.big.set(t("p_layer", n=layers + 1) if rec else t("p_layers", n=layers), FELT if rec else IVORY)
            sec = "%.1f s" % length if length else ""
            self.side.set(t("p_loop", s=sec) if p.get("looping") else t("p_take", s=sec))
        self.keys.lit_colour = ui.K_REC if rec else ui.K_LIT
        self.a_hint.set({"empty": t("h_rec"), "recording": t("h_loop"), "playing": t("h_overdub"),
                         "stopped": t("h_rec")}.get(s, ""))
        self.b_hint.set({"recording": t("h_end"), "playing": t("h_stop")}.get(s, t("h_leave")))


def performance():
    r = link.request("mode", mode="performance")
    if not report(r):
        return
    view = Perf()
    view.update(link.st())
    view.show()
    link.keys = view.keys
    try:
        while True:
            k = next_key(None, view.update)
            st = link.st()
            if st is None:
                continue
            p = st.get("perf") or {}
            s = p.get("state", "empty")
            if k == A:
                report_quietly(link.request({"empty": "perf_record", "recording": "perf_close_loop",
                                             "playing": "perf_overdub"}.get(s, "perf_record")))
            elif k == B:
                if s == "recording":
                    report_quietly(link.request("perf_take"))
                elif s == "playing":
                    report_quietly(link.request("perf_play"))
                elif not p.get("layers") or choose(t("clear_q"), [t("c_keep"), t("c_clear")]) == 1:
                    return
                view.show()
            elif k == UP:
                report_quietly(link.request("perf_undo"))
            elif k == DOWN:
                report_quietly(link.request("perf_redo"))
            elif k == START and p.get("layers"):
                save_take()
                view.show()
            elif k == SELECT:
                guarded(settings)
                view.show()
    finally:
        link.keys = None
        link.request("mode", mode="library")          # leaving clears the loop (asked first)


def save_take():
    title = keyboard(t("save_title"))
    if title is None:
        return
    r = link.request("perf_save", **({"title": title} if title else {}))
    if r and r.get("ok"):
        say(t("saved"), r.get("saved", ""), GREEN)
    else:
        report(r)


# --- the keyboard, for names and passwords ------------------------------------------------------
KEYSETS = ("abcdefghijklmnopqrstuvwxyz0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
SYMBOLS = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"     # passwords: a third set
OK_KEY = "\x00"                                     # the last cell of every set: done
COLS = 12


def keyboard(title, symbols=False):
    """The one screen where A and B type: d-pad picks a key, A types it, B deletes (on empty:
    back, None), START changes the character set, the OK cell finishes."""
    sets = KEYSETS + (SYMBOLS,) if symbols else KEYSETS
    s, ks, pos = "", 0, 0
    view = ui.Screen()
    view.text(4, 2, "label", BRASS, text=title.upper())
    typed = view.text(4, 14, "body_b", IVORY)
    view.box(0, 31, W, 1, LINE)
    cursor = view.box(0, 0, 12, 15, 0x305080)
    cells = [view.text(6 + (i % COLS) * 13, 34 + (i // COLS) * 15, "body_b", MUTED) for i in range(len(SYMBOLS) + 2)]
    view.hints([("A", t("h_type")), ("B", t("h_del")), ("ST", t("h_abc"))])

    def draw():
        chars = sets[ks] + " " + OK_KEY
        for i, cell in enumerate(cells):
            c = chars[i] if i < len(chars) else ""
            cell.set("OK" if c == OK_KEY else ("_" if c == " " else c),
                     (GREEN if c == OK_KEY else IVORY) if i == pos else MUTED)
        cursor.place(3 + (pos % COLS) * 13, 33 + (pos // COLS) * 15, 20 if chars[pos] == OK_KEY else 12, 15)
        typed.set((s[-22:] if not symbols else "•" * min(len(s), 22)) + "_")
        return chars

    chars = draw()
    view.show()
    while True:
        k = next_key()
        n = len(chars)
        if k == RIGHT:
            pos = (pos + 1) % n
        elif k == LEFT:
            pos = (pos - 1) % n
        elif k == DOWN:
            pos = pos + COLS if pos + COLS < n else pos % COLS
        elif k == UP:
            pos = pos - COLS if pos >= COLS else min(n - 1, pos + COLS * ((n - 1 - pos) // COLS))
        elif k in (A, A_HELD):
            if chars[pos] == OK_KEY:
                return s
            s += chars[pos]
        elif k == B:
            if not s:
                return None
            s = s[:-1]
        elif k == START:
            ks = (ks + 1) % len(sets)
            pos = min(pos, len(sets[ks]) + 1)
        elif k == SELECT:
            return None
        chars = draw()


# --- calibration ----------------------------------------------------------------------------
CAL_ACTIVE = ("waiting", "preflight", "measuring", "paused")
CAL_DELAYS = (0, 10, 30)
MAP = {".": ui.K_TODO, "o": None, "*": ui.K_NOW, "!": ui.K_BAD}


def calibrating(st):
    c = st.get("calib")
    return bool(c) and c.get("phase") in CAL_ACTIVE


class CalView(ui.Screen):
    def __init__(self):
        super().__init__()
        self.text(4, 4, "label", BRASS, text=t("cal"))
        self.left = self.text(W - 4, 4, "label", MUTED, (1, 0))
        self.key = self.text(4, 15, "big", IVORY, room=60)
        self.what = self.text(58, 22, "body", MUTED, room=W - 62)
        self.keys = ui.Keys(self, 48, 22)
        self.count = self.text(4, 76, "body_b", IVORY)
        self.bad = self.text(W - 4, 76, "body", FELT, (1, 0))
        self.msg = self.text(4, 92, "body", MUTED)
        self.hints([("B", t("h_cancel"))])

    def update(self, st):
        c = (st or {}).get("calib") or {}
        phase = c.get("phase")
        eta = c.get("eta")
        if phase == "waiting":
            self.left.set(t("cal_starts"))
            self.key.set(layout.mmss(c.get("starts_in", 0)))
            self.what.set("")
        else:
            self.left.set(t("cal_left", m=(eta + 59) // 60) if eta is not None else "")
            self.key.set(c.get("key") or "")
            self.what.set({"preflight": t("cal_check"), "paused": t("cal_paused")}.get(phase, c.get("step") or ""))
        keymap = c.get("keys") or ""
        for i, ch in enumerate(keymap[:88]):
            k = layout.KEY_LO + i
            v = MAP.get(ch, ui.K_TODO)
            if v is None:
                v = ui.K_DONE_B if layout.is_black(k) else ui.K_DONE_W
            self.keys.set(k, v)
        self.count.set(t("cal_keys", n=c.get("done", 0), of=c.get("of", 88)))
        self.bad.set(t("cal_flagged", n=c["flags"]) if c.get("flags") else "")
        self.msg.set(c.get("message") or t("cal_quiet"), ORANGE if c.get("message") else MUTED)


def calibration():
    """The calibration screen while one runs, then its result, once."""
    view = CalView()
    view.update(link.st())
    view.show()
    shown = 0
    while True:
        tick()
        st = link.st()
        c = (st or {}).get("calib") or {}
        if st is not None and c.get("phase") not in CAL_ACTIVE:
            calibration_result(c)
            return
        if time.monotonic() - shown > 0.2:
            shown = time.monotonic()
            view.update(st)
        k = inp.key()
        if k == B:
            if choose(t("cal_cancel_q"), [t("c_go_on"), t("c_stop")]) == 1:
                report_quietly(link.request("calibrate_cancel"))
            view.show()
        time.sleep(0.03)


def calibration_result(c):
    phase = c.get("phase")
    if phase == "done":
        r = link.request("calibrate_summary", limit=40)
        lines = []
        for line in (r or {}).get("lines") or [t("cal_done")]:
            lines += layout.wrap(line, W - 14, lambda s: ui.width("body_b", s), 3)
        ListView(t("cal_done").upper(), [(l,) for l in lines], 0, hints=[("B", t("h_back"))]).run()
    elif phase == "failed":
        say(t("cal_failed"), c.get("message", ""), FELT, 6)
    elif phase == "cancelled":
        say(t("cal_cancelled"), "", MUTED, 1.5)


def calibrate():
    """Measure every key: up to an hour, so it can start later and run with nobody home."""
    mic = (link.st() or {}).get("mic") or {}
    if not mic.get("ok"):
        say(t("cal_no_mic"), mic.get("why", ""), ORANGE, 3)
        return
    i = choose(t("cal_when").upper(), [t("cal_now") if n == 0 else t("cal_in", n=n) for n in CAL_DELAYS])
    if i is None:
        return
    if report(link.request("calibrate", delay=CAL_DELAYS[i])):
        raise GoHome()               # home sees it running and shows it


# --- settings ---------------------------------------------------------------------------------
def settings():
    sel = 0
    while True:
        st = link.st() or {}
        net = (link.request("net", timeout=6) or {}).get("net") or {}
        wifi = net.get("wifi") or {}
        mic = st.get("mic") or {}
        rows = [(t("set_wifi"), wifi.get("ssid") or wifi.get("state") or t("v_offline"),
                 GREEN if wifi.get("ssid") else FELT),
                (t("set_address"), net.get("ip") or "-", None),
                (t("set_library"), t("v_works", n=st.get("works", "-")), None),
                (t("set_calibrate"), "" if mic.get("ok") else t("v_needs_mic"), None if mic.get("ok") else FAINT),
                (t("set_mic"), mic.get("name") if mic.get("ok") else t("v_none"), None if mic.get("ok") else FELT),
                (t("set_piano"), t("v_ok") if st.get("midi_out") else t("v_lost"), None if st.get("midi_out") else FELT),
                (t("set_link"), t("v_ok") if link.connected() else t("v_lost"), None)]
        k, i = ListView(t("settings"), rows, sel).run()
        if k == B:
            return
        sel = i
        if i in (0, 1):
            network()
        elif i == 2:
            sync()
        elif i == 3:
            calibrate()


def sync():
    ui.message(t("syncing"), "", IVORY)
    r = link.request("sync", timeout=120)
    if r and r.get("ok"):
        say(t("synced"), t("synced_body", works=r.get("works", "?"), fetched=r.get("fetched", "?")), GREEN, 2)
    else:
        report(r)


def network():
    sel = 2
    while True:
        ui.message(t("net_checking"), "", MUTED)
        net = (link.request("net", timeout=15) or {}).get("net") or {}
        wifi = net.get("wifi") or {}
        rows = [(t("set_wifi"), wifi.get("ssid") or t("v_offline"), GREEN if wifi.get("ssid") else FELT),
                (t("net_state"), wifi.get("state") or net.get("error") or "-", None),
                (t("net_ip"), net.get("ip") or "-", None),
                (t("net_join"), None, BRASS), (t("net_log"), None, None)]
        k, i = ListView(t("net"), rows, sel).run()
        if k == B:
            return
        sel = i
        if i == 3:
            join_network()
        elif i == 4:
            wifi_log()


def join_network():
    ui.message(t("scanning"), "", MUTED)
    r = link.request("wifi_scan", timeout=40)
    nets = (r or {}).get("networks") or []
    if not nets:
        say(t("no_nets"), t("no_nets_body"), ORANGE, 2)
        return
    k, i = ListView(t("choose_net"), [(n["ssid"], "%d%%" % n["signal"]) for n in nets], 0).run()
    if k == B:
        return
    n = nets[i]
    psk = keyboard(t("password", s=n["ssid"][:14]), symbols=True) if n["secure"] else ""
    if psk is None:
        return
    ui.message(t("joining"), n["ssid"], IVORY)
    report(link.request("wifi_join", timeout=100, ssid=n["ssid"], psk=psk), "ok_wifi")


def wifi_log():
    """NetworkManager's last lines, newest at the bottom: why a join failed."""
    ui.message(t("net_reading"), "", MUTED)
    lines, failed = fetch("wifi_log", "lines")
    if lines is None:
        report(failed)
        return
    rows = []
    for line in lines:
        rows += [(l,) for l in layout.wrap(line, W - 12, lambda s: ui.width("body", s), 4)]
    ListView(t("net_log").upper(), rows or [("-",)], max(0, len(rows) - 1), hints=[("B", t("h_back"))]).run()


# --- main -------------------------------------------------------------------------------
def guarded(fn):
    """Run a screen. GoHome just returns; any other failure says so briefly, logs why, and goes
    back. Never a crash screen."""
    try:
        fn()
    except ShowNowPlaying:
        gc.collect()
        guarded(now_playing)
    except GoHome:
        pass
    except Exception as e:   # noqa: BLE001 - anything at all
        traceback.print_exception(e)
        log_error(e)
        say(t("sorry"), t("sorry_body"), ORANGE, 2.5)


ui.load_fonts()                 # all of them now, while the heap is in one piece (see ui)
gc.collect()
print("free memory at start:", gc.mem_free())
ui.DISPLAY.brightness = 1.0
link.send("status")
while True:
    try:
        if link.st() is None:
            ui.message(t("no_contact"), t("no_contact_body"), ORANGE)
            end = time.monotonic() + 3
            while link.st() is None and time.monotonic() < end:
                tick()
                time.sleep(0.05)
            if link.st() is None:
                link.send("status")
                continue
        home()
    except Exception as e:   # noqa: BLE001
        traceback.print_exception(e)
        log_error(e)
        say(t("sorry"), t("sorry_body"), ORANGE, 1.5)
