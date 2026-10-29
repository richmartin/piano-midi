# Piano control panel: Adafruit PyBadge LC on the Pi's USB port, on the piano's music desk.
#
# The Pi (pianod) is the source of truth. This shows what pianod pushes, and sends it requests:
# newline-delimited JSON over CircuitPython's second USB serial port (boot.py enables it).
# Same bones as the HotPi panel, deliberately.
#
# Buttons mean the same everywhere, so they can be labelled on the enclosure:
#   A       the "do music" button: play/pause - and in Performance mode record/loop/overdub
#   B       stop (in Performance mode: end the take / stop / play; calibrating: cancel)
#   START   shuffle everything (in Performance mode: save the take)
#   SELECT  menu (in a menu: straight back to the home screen)
#   d-pad   home: UP/DOWN volume, LEFT/RIGHT previous/next piece
#           menus: UP/DOWN move, RIGHT chooses, LEFT back
#           Performance mode: UP undo, DOWN redo
# (The keyboard - naming a take, a WiFi password - is the one exception: A types, B deletes.)
# The screen dims after a minute; the first press only wakes it.

import json
import time
import traceback

import board
import displayio
import keypad
import terminalio
import usb_cdc
from adafruit_display_text import label

import layout
from layout import H, W
from texts import t

# --- hardware ---------------------------------------------------------------------------
DISPLAY = board.DISPLAY
KEYS = keypad.ShiftRegisterKeys(clock=board.BUTTON_CLOCK, data=board.BUTTON_OUT, latch=board.BUTTON_LATCH,
                                key_count=8, value_when_pressed=True)
B, A, START, SELECT, RIGHT, DOWN, UP, LEFT = range(8)
LINK = usb_cdc.data
FONT = terminalio.FONT

WHITE, GREY, DIM, RED, ORANGE, GREEN, CYAN = 0xFFFFFF, 0x9A9A9A, 0x505050, 0xFF3030, 0xFF9020, 0x30E060, 0x40C0FF
IDLE_DIM_S = 60


# --- link to pianod ----------------------------------------------------------------------
class Link:
    """Requests out, replies and state pushes in. Never blocks the UI for long."""

    def __init__(self):
        self.buf = b""
        self.next_id = 1
        self.replies = {}
        self.state = None
        self.last_heard = -1000.0

    def send(self, cmd, **kw):
        rid = self.next_id
        self.next_id += 1
        kw["cmd"], kw["id"] = cmd, rid
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
            if msg.get("type") == "state":
                self.state = msg
            elif "id" in msg:
                self.replies[msg["id"]] = msg
        if len(self.buf) > 4096:    # garbage: start afresh
            self.buf = b""

    def connected(self):
        return time.monotonic() - self.last_heard < 15

    def request(self, cmd, timeout=8.0, **kw):
        """Send and wait for the reply (keeps polling, so pushes still land). None on timeout."""
        rid = self.send(cmd, **kw)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.poll()
            if rid in self.replies:
                return self.replies.pop(rid)
            time.sleep(0.02)
        return None


link = Link()


def fetch(cmd, key, **kw):
    """A whole list, a page at a time: pianod keeps each reply under the 4 KB a line may be.
    The failed reply (or None) if a page doesn't come."""
    items, offset = [], 0
    while offset is not None:
        r = link.request(cmd, offset=offset, **kw)
        if not r or not r.get("ok"):
            return None, r
        items += r[key]
        offset = r.get("next")
    return items, None


# --- drawing helpers --------------------------------------------------------------------
def text(group, s, x, y, color=WHITE, scale=1, anchor=(0, 0)):
    """A label. Never drawn past the screen's edge: too long, and it is shortened to fit."""
    lb = label.Label(FONT, text=layout.fit(str(s), x, scale, anchor[0]), color=color, scale=scale)
    lb.anchor_point = anchor
    lb.anchored_position = (x, y)
    group.append(lb)
    return lb


def rect(group, x, y, w, h, color):
    if w <= 0 or h <= 0:
        return
    bmp = displayio.Bitmap(w, h, 1)
    pal = displayio.Palette(1)
    pal[0] = color
    group.append(displayio.TileGrid(bmp, pixel_shader=pal, x=x, y=y))


def screen():
    g = displayio.Group()
    DISPLAY.root_group = g
    return g


def message(title, body="", color=WHITE, wait=1.5):
    g = screen()
    text(g, title, W // 2, 40, color, 2 if len(title) <= 13 else 1, (0.5, 0.5))
    y = 66
    for line in wrap(body, 26)[:4]:
        text(g, line, W // 2, y, GREY, 1, (0.5, 0))
        y += 13
    if wait:
        sleep_polling(wait)


def wrap(s, width):
    out, line = [], ""
    for word in str(s).split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return out


def sleep_polling(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        link.poll()
        time.sleep(0.02)


def hint(g, s):
    text(g, s, W // 2, H - 2, DIM, 1, (0.5, 1.0))


def progress(g, y, frac):
    rect(g, layout.MARGIN, y, W - 2 * layout.MARGIN, 5, 0x202830)
    rect(g, layout.MARGIN, y, layout.bar_fill(frac, W - 2 * layout.MARGIN), 5, CYAN)


# --- input ------------------------------------------------------------------------------
class Input:
    def __init__(self):
        self.last = time.monotonic()
        self.dimmed = False
        self.held = None
        self.next_rep = 0

    def key(self):
        """The next key pressed, or None. Repeats while UP/DOWN/LEFT/RIGHT are held."""
        ev = KEYS.events.get()
        now = time.monotonic()
        if ev and ev.pressed:
            self.last = now
            self.held, self.next_rep = ev.key_number, now + 0.45
            if self.dimmed:
                DISPLAY.brightness = 1.0
                self.dimmed = False
                return None          # the first press only wakes the screen
            return ev.key_number
        if ev and ev.released:
            self.held = None
            return None
        if self.held in (UP, DOWN, LEFT, RIGHT) and now >= self.next_rep:
            self.next_rep = now + 0.08
            self.last = now
            return self.held
        if not self.dimmed and now - self.last > IDLE_DIM_S:
            DISPLAY.brightness = 0.08
            self.dimmed = True
        return None


inp = Input()


def wait_key():
    while True:
        link.poll()
        k = inp.key()
        if k is not None:
            return k
        time.sleep(0.01)


# --- menus ------------------------------------------------------------------------------
class GoHome(Exception):
    """Leave every menu at once: SELECT from anywhere."""


def choose(title, options, start=0, hint_text=None, dim=()):
    """A scrolling list: up/down move, right chooses, left goes back (None). Rows in `dim` are
    greyed out (still choosable: the caller says why they can't be done)."""
    sel, top = min(start, len(options) - 1), 0
    while True:
        g = screen()
        text(g, title[:26], 4, 2, CYAN)
        rect(g, 0, 14, W, 1, DIM)
        top = layout.list_window(sel, top)
        for i in range(top, min(len(options), top + layout.LIST_ROWS)):
            y = 18 + (i - top) * 13
            if i == sel:
                rect(g, 0, y - 1, W, 13, 0x203050)
            color = (GREY if i == sel else DIM) if i in dim else (WHITE if i == sel else GREY)
            text(g, str(options[i])[:26], 6, y, color)
        hint(g, hint_text or t("hint_choose"))
        k = wait_key()
        if k == SELECT:
            raise GoHome()
        if k == UP:
            sel = (sel - 1) % len(options)
        elif k == DOWN:
            sel = (sel + 1) % len(options)
        elif k == RIGHT:
            return sel
        elif k == LEFT:
            return None


KEYSETS = ("abcdefghijklmnopqrstuvwxyz0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
SYMBOLS = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"     # passwords: a third set
OK_KEY = "\x00"     # the last cell of every set: done


def keyboard(title, symbols=False):
    """The one screen where A and B type: d-pad picks a key, A types it, B deletes (on empty:
    back), START changes the character set, the OK cell finishes. SELECT still goes home."""
    s, ks, pos, cols = "", 0, 0, 12
    sets = KEYSETS + (SYMBOLS,) if symbols else KEYSETS
    while True:
        chars = sets[ks] + " " + OK_KEY
        g = screen()
        text(g, title[:26], 4, 2, CYAN)
        text(g, s[-24:] + "_", 4, 18, WHITE)
        rect(g, 0, 30, W, 1, DIM)
        for i, c in enumerate(chars):
            x, y = 6 + (i % cols) * 13, 34 + (i // cols) * 15
            if i == pos:
                rect(g, x - 3, y - 2, 12 if c != OK_KEY else 20, 15, 0x305080)
            shown = "OK" if c == OK_KEY else (c if c != " " else "_")
            text(g, shown, x, y, (GREEN if c == OK_KEY else WHITE) if i == pos else GREY)
        text(g, t("hint_kb_sym" if symbols else "hint_kb"), W // 2, H - 14, DIM, 1, (0.5, 1.0))
        hint(g, t("hint_kb2"))
        k = wait_key()
        if k == SELECT:
            raise GoHome()
        n = len(chars)
        if k == RIGHT:
            pos = (pos + 1) % n
        elif k == LEFT:
            pos = (pos - 1) % n
        elif k == DOWN:
            pos = pos + cols if pos + cols < n else pos % cols
        elif k == UP:
            pos = pos - cols if pos >= cols else min(n - 1, pos + cols * ((n - 1 - pos) // cols))
        elif k == A:
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


def report(r, ok_key="ok"):
    if r is None:
        message(t("no_answer"), t("no_answer_body"), ORANGE)
    elif r.get("ok"):
        message(t(ok_key), "", GREEN, 0.8)
    else:
        message(t("not_done"), r.get("error", ""), RED, 2.5)


# --- the home screen (library mode) --------------------------------------------------------
def home():
    shown = None
    while True:
        link.poll()
        st = link.state if link.connected() else None
        key = None
        if st:
            key = (st["playing"], st.get("title"), int(st.get("pos", 0)), st.get("volume"),
                   st.get("next_title"), st.get("midi_out"), st.get("mode"))
        if st and calibrating_now(st):
            # A calibration owns the piano (started here, from the CLI, or on a timer).
            guarded(calibration)
            shown = None
            continue
        if (key, link.connected()) != shown:
            shown = (key, link.connected())
            draw_home(st)
        k = inp.key()
        if k is None:
            time.sleep(0.03)
            continue
        if st is None:
            continue
        if st.get("mode") == "performance":
            # pianod was put in performance mode by someone else (the CLI, the web): join in.
            guarded(performance)
            shown = None
            continue
        if k == A:
            report_quietly(link.request("toggle"))
        elif k == B:
            report_quietly(link.request("stop"))
        elif k == RIGHT:
            report_quietly(link.request("next"))
        elif k == LEFT:
            report_quietly(link.request("prev"))
        elif k in (UP, DOWN):
            v = st.get("volume", 8) + (1 if k == UP else -1)
            if 1 <= v <= 11:
                report_quietly(link.request("volume", v=v))
        elif k == START:
            report_quietly(link.request("play", all=True, shuffle=True))
        elif k == SELECT:
            guarded(menu)
            shown = None


def report_quietly(r):
    """Home-screen actions: only failures are worth a screen."""
    if r is not None and not r.get("ok"):
        message(t("not_done"), r.get("error", ""), RED, 2.0)


def draw_home(st):
    g = screen()
    if st is None:
        lines = t("no_contact")
        text(g, lines[0], W // 2, 40, ORANGE, 1, (0.5, 0.5))
        for i, line in enumerate(lines[1:]):
            text(g, line, W // 2, 60 + 13 * i, GREY, 1, (0.5, 0))
        return
    if st.get("title"):
        playing = st["playing"]
        text(g, t("playing") if playing else t("paused"), W // 2, 4, GREEN if playing else ORANGE, 1, (0.5, 0))
        text(g, st["title"], W // 2, 24, WHITE, 2 if len(st["title"]) <= 13 else 1, (0.5, 0.5))
        text(g, st.get("composer") or "", W // 2, 40, GREY, 1, (0.5, 0))
        frac = st["pos"] / st["len"] if st.get("len") else 0
        progress(g, 58, frac)
        text(g, layout.mmss(st["pos"]), layout.MARGIN, 66, DIM)
        text(g, layout.mmss(st.get("len", 0)), W - layout.MARGIN, 66, DIM, 1, (1.0, 0))
        text(g, "%d/%d" % (st["index"] + 1, st["count"]), W // 2, 66, DIM, 1, (0.5, 0))
        if st.get("next_title"):
            text(g, t("next", s=st["next_title"])[:26], W // 2, 82, GREY, 1, (0.5, 0))
    else:
        text(g, t("idle"), W // 2, 34, WHITE, 1, (0.5, 0.5))
        text(g, t("idle_hint"), W // 2, 52, GREY, 1, (0.5, 0))
    text(g, t("vol", n=st.get("volume", "-")), layout.MARGIN, 98, CYAN)
    if not st.get("midi_out"):
        rect(g, 0, 110, W, 14, 0x601010)
        text(g, t("no_piano"), W // 2, 112, WHITE, 1, (0.5, 0))
    else:
        hint(g, t("hint_home"))


# --- menus and browsing ---------------------------------------------------------------------
def menu():
    entries = (("m_browse", browse), ("m_recordings", recordings), ("m_perform", performance),
               ("m_shuffle", shuffle_all), ("m_sync", sync), ("m_calibrate", calibrate),
               ("m_network", network), ("m_info", info))
    start = 0
    while True:
        # Calibrate is greyed out until pianod has found a mic that works: a calibration set
        # going for later must not discover then that it can't hear.
        mic_ok = ((link.state or {}).get("mic") or {}).get("ok")
        labels = [t("m_calibrate_nomic") if key == "m_calibrate" and not mic_ok else t(key)
                  for key, _fn in entries]
        dim = () if mic_ok else tuple(n for n, (key, _fn) in enumerate(entries) if key == "m_calibrate")
        i = choose(t("menu"), labels, start, dim=dim)
        if i is None:
            return
        start = i
        entries[i][1]()


def browse():
    comps, failed = fetch("composers", "composers")
    if comps is None:
        report(failed)
        return
    sel = 0
    while True:
        i = choose(t("menu"), ["%s (%d)" % (c["name"], c["works"]) for c in comps], sel)
        if i is None:
            return
        sel = i
        browse_works(comps[i])


def browse_works(comp):
    works, failed = fetch("works", "works", slug=comp["slug"])
    if works is None:
        report(failed)
        return
    sel = 0
    while True:
        i = choose(t("works_of", s=comp["name"]), [w["title"] for w in works], sel)
        if i is None:
            return
        sel = i
        w = works[i]
        what = choose(w["title"], [t("w_play"), t("w_play_all"), t("w_shuffle_all")])
        if what == 0:
            r = link.request("play", ids=[w["id"]])
        elif what == 1:
            r = link.request("play", slug=comp["slug"])
        elif what == 2:
            r = link.request("play", slug=comp["slug"], shuffle=True)
        else:
            continue
        report_quietly(r)
        raise GoHome()


def recordings():
    works, failed = fetch("recordings", "works")
    if works is None:
        report(failed)
        return
    if not works:
        message(t("no_recordings"), t("no_recordings_body"), GREY)
        return
    i = choose(t("m_recordings"), [w["title"] for w in works])
    if i is None:
        return
    report_quietly(link.request("play", ids=[works[i]["id"]]))
    raise GoHome()


def shuffle_all():
    report_quietly(link.request("play", all=True, shuffle=True))
    raise GoHome()


def sync():
    message(t("syncing"), "", WHITE, 0)
    r = link.request("sync", timeout=120)
    if r and r.get("ok"):
        message(t("synced"), t("synced_body", works=r.get("works", "?"), fetched=r.get("fetched", "?")), GREEN)
    else:
        report(r)


def info():
    st = link.state or {}
    lines = [t("i_link", s=t("i_ok") if link.connected() else t("i_lost")),
             t("i_midi", s=t("i_ok") if st.get("midi_out") else t("i_lost")),
             t("i_works", n=st.get("works", "-")),
             t("i_mic", s=(st.get("mic") or {}).get("name") if (st.get("mic") or {}).get("ok")
               else t("i_none")),
             t("i_mode", s=st.get("mode", "-"))]
    choose(t("info_title"), lines, 0, t("hint_back"))


# --- network: the panel is the Pi's only screen ----------------------------------------------
def network():
    sel = 3
    while True:
        message(t("net_checking"), "", WHITE, 0)
        r = link.request("net", timeout=15)
        net = (r or {}).get("net") or {}
        wifi = net.get("wifi") or {}
        state = wifi.get("state") or net.get("error") or "-"
        lines = [t("wifi", s=wifi.get("ssid") or t("not_connected")), t("net_state", s=state),
                 t("ip", s=net.get("ip") or "-"), t("join"), t("net_log"), t("refresh")]
        i = choose(t("net_title"), lines, sel)
        if i is None:
            return
        sel = i
        if i == 3:
            join_network()
        elif i == 4:
            wifi_log()


def join_network():
    message(t("scanning"), "", WHITE, 0)
    r = link.request("wifi_scan", timeout=40)
    nets = (r or {}).get("networks") or []
    if not nets:
        message(t("no_nets"), t("no_nets_body"), ORANGE)
        return
    i = choose(t("choose_net"), ["%s %d%%" % (n["ssid"][:20], n["signal"]) for n in nets])
    if i is None:
        return
    n = nets[i]
    psk = keyboard(t("password", s=n["ssid"][:14]), symbols=True) if n["secure"] else ""
    if psk is None:
        return
    message(t("joining"), n["ssid"], WHITE, 0)
    report(link.request("wifi_join", timeout=100, ssid=n["ssid"], psk=psk), "ok_wifi")


def wifi_log():
    """NetworkManager's last lines, newest at the bottom: why a join failed."""
    message(t("net_reading"), "", WHITE, 0)
    lines, failed = fetch("wifi_log", "lines")
    if lines is None:
        report(failed)
        return
    rows = []
    for line in lines:
        rows += wrap(line, 26) or [""]
    choose(t("net_log"), rows or ["-"], max(0, len(rows) - 1), t("hint_back"))


# --- performance mode: the loop station -----------------------------------------------------
def performance():
    r = link.request("mode", mode="performance")
    if r is None or not r.get("ok"):
        report(r)
        return
    try:
        perf_loop()
    finally:
        link.request("mode", mode="library")       # leaving clears the loop (it warns first)


def perf_loop():
    shown = None
    while True:
        link.poll()
        st = link.state if link.connected() else None
        p = (st or {}).get("perf") or {}
        key = (p.get("state"), p.get("layers"), p.get("overdub"), int(p.get("pos", 0) * 4),
               p.get("can_redo"), link.connected())
        if key != shown:
            shown = key
            draw_perf(st, p)
        k = inp.key()
        if k is None:
            time.sleep(0.02)
            continue
        if st is None:
            continue
        state = p.get("state", "empty")
        if k == A:
            if state == "empty":
                report_quietly(link.request("perf_record"))
            elif state == "recording":
                report_quietly(link.request("perf_close_loop"))
            elif state == "playing":
                report_quietly(link.request("perf_overdub"))
            else:
                report_quietly(link.request("perf_record"))     # pianod explains if it can't
        elif k == B:
            if state == "recording":
                report_quietly(link.request("perf_take"))
            else:
                report_quietly(link.request("perf_play"))
        elif k == UP:
            report_quietly(link.request("perf_undo"))
        elif k == DOWN:
            report_quietly(link.request("perf_redo"))
        elif k == START:
            if p.get("layers"):
                save_take()
                shown = None
        elif k == SELECT:
            if not p.get("layers") or confirm_leave():
                return
            shown = None


def confirm_leave():
    try:
        return choose(t("clear_q"), [t("c_keep"), t("c_clear")]) == 1
    except GoHome:
        return False


def save_take():
    try:
        title = keyboard(t("save_title"))
    except GoHome:
        return
    if title is None:
        return
    r = link.request("perf_save", **({"title": title} if title else {}))
    if r and r.get("ok"):
        message(t("saved"), r.get("saved", ""), GREEN)
    else:
        report(r)


def draw_perf(st, p):
    g = screen()
    text(g, t("perf_title"), W // 2, 2, CYAN, 1, (0.5, 0))
    if st is None:
        text(g, t("no_contact")[0], W // 2, 50, ORANGE, 1, (0.5, 0.5))
        return
    state = p.get("state", "empty")
    overdub = p.get("overdub")
    word, color, body = {
        "empty": ("p_empty", GREY, t("p_empty_body")),
        "recording": ("p_recording", RED, t("p_recording_body")),
        "playing": ("p_overdub" if overdub else "p_playing", RED if overdub else GREEN,
                    t("p_overdub_hint") if overdub else t("p_playing_hint")),
        "stopped": ("p_stopped", ORANGE, t("p_stopped_hint")),
    }.get(state, ("p_empty", GREY, ""))
    text(g, t(word), W // 2, 34, color, 2, (0.5, 0.5))
    if p.get("layers"):
        text(g, t("p_layers", n=p["layers"], s="s" if p["layers"] != 1 else "",
                  t=t("p_loop") if p.get("looping") else t("p_take")), W // 2, 54, WHITE, 1, (0.5, 0))
    if p.get("length"):
        progress(g, 72, (p.get("pos", 0) / p["length"]) if p["length"] else 0)
        text(g, layout.mmss(p.get("pos", 0)), layout.MARGIN, 80, DIM)
        text(g, layout.mmss(p["length"]), W - layout.MARGIN, 80, DIM, 1, (1.0, 0))
    text(g, body, W // 2, 98, GREY, 1, (0.5, 0))
    hint(g, t("hint_perf"))


# --- calibration ----------------------------------------------------------------------------
CAL_ACTIVE = ("waiting", "preflight", "measuring", "paused")
CAL_DELAYS = (0, 10, 30)


def calibrating_now(st):
    c = st.get("calib")
    return bool(c) and c.get("phase") in CAL_ACTIVE


def calibrate():
    """Measure every key — up to an hour, so it can start later and run with nobody home."""
    mic = (link.state or {}).get("mic") or {}
    if not mic.get("ok"):
        message(t("cal_no_mic"), mic.get("why", ""), ORANGE, 3)
        return
    i = choose(t("cal_when"), [t("cal_now") if n == 0 else t("cal_in", n=n) for n in CAL_DELAYS])
    if i is None:
        return
    r = link.request("calibrate", delay=CAL_DELAYS[i])
    if r is None or not r.get("ok"):
        report(r)
        return
    raise GoHome()          # home sees it running and shows it


def calibration():
    """The calibration screen, while one runs; then its result, once."""
    shown = None
    while True:
        link.poll()
        st = link.state if link.connected() else None
        c = (st or {}).get("calib") or {}
        phase = c.get("phase")
        if st is not None and phase not in CAL_ACTIVE:
            calibration_result(c)
            return
        key = (phase, c.get("starts_in"), c.get("done"), c.get("eta"), c.get("message"),
               link.connected())
        if key != shown:
            shown = key
            draw_calibration(st, c)
        k = inp.key()
        if k is None:
            time.sleep(0.05)
            continue
        if k == B:
            try:
                stop = choose(t("cal_cancel_q"), [t("c_go_on"), t("c_stop")]) == 1
            except GoHome:
                stop = False
            if stop:
                report_quietly(link.request("calibrate_cancel"))
            shown = None


def draw_calibration(st, c):
    g = screen()
    text(g, t("cal_title"), W // 2, 2, CYAN, 1, (0.5, 0))
    if st is None:
        text(g, t("no_contact")[0], W // 2, 50, ORANGE, 1, (0.5, 0.5))
        return
    phase = c.get("phase")
    if phase == "waiting":
        text(g, t("cal_waiting"), W // 2, 26, GREY, 1, (0.5, 0.5))
        text(g, layout.mmss(c.get("starts_in", 0)), W // 2, 46, WHITE, 3, (0.5, 0.5))
        text(g, t("cal_leave"), W // 2, 76, GREY, 1, (0.5, 0))
    else:
        word = {"preflight": t("cal_preflight"), "paused": t("cal_paused")}.get(
            phase, t("cal_measuring", k=c.get("key") or ""))
        text(g, word, W // 2, 30, ORANGE if phase == "paused" else WHITE, 2, (0.5, 0.5))
        n, of = c.get("done", 0), c.get("of", 88) or 88
        progress(g, 50, n / of)
        text(g, t("cal_keys", n=n, of=of), W // 2, 58, GREY, 1, (0.5, 0))
        if c.get("eta") is not None:
            text(g, t("cal_eta", m=(c["eta"] + 59) // 60), W // 2, 72, GREY, 1, (0.5, 0))
        y = 88
        for line in wrap(c.get("message") or "", 26)[:2]:
            text(g, line, W // 2, y, ORANGE, 1, (0.5, 0))
            y += 12
    hint(g, t("hint_cal"))


def calibration_result(c):
    phase = c.get("phase")
    if phase == "done":
        r = link.request("calibrate_summary", limit=40)
        lines = [part for line in ((r or {}).get("lines") or [t("cal_done")])
                 for part in wrap(line, 26)]
        choose(t("cal_done"), lines, 0, t("hint_back"))
    elif phase == "failed":
        message(t("cal_failed"), c.get("message", ""), RED, 0)
        wait_key()
    elif phase == "cancelled":
        message(t("cal_cancelled"), "", GREY, 1.5)


# --- main -------------------------------------------------------------------------------
def guarded(fn):
    """Run a screen. GoHome just returns; any other failure says so briefly, logs why, and goes
    back home. Never a crash screen."""
    try:
        fn()
    except GoHome:
        pass
    except Exception as e:   # noqa: BLE001 - anything at all
        traceback.print_exception(e)
        message(t("sorry"), t("sorry_body"), ORANGE, 2.5)


DISPLAY.brightness = 1.0
link.send("status")
while True:
    try:
        home()
    except Exception as e:   # noqa: BLE001
        traceback.print_exception(e)
        message(t("restart"), "", ORANGE, 1.5)
