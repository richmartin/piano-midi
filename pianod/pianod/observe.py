"""Every note the solenoids play, against what the key sensors saw: calibration data for free.

The piano echoes its own playback on MIDI OUT (docs/design.md), so each note-on pianod sends is a
sample of what calibration measures: how long it took to strike (send -> echo), how hard the
sensors say it landed, or that it never sounded (no echo). Real music adds what a calibration run
never plays: chords, the pedal at length, a long piece warming the solenoids.

This only records; nothing feeds back into the playback model (yet). One JSON row per note, one
file per day under DATA/observe/, kept KEEP_DAYS:

  [wall time, key, velocity sent, sustain down, notes already sounding,
   strike ms (send -> echo; null: never sounded), sensor velocity, ms held if released before
   it sounded, strike ms the calibration predicted, source]

A long run of notes with no echo at all means nothing is listening (the wagon off, MIDI not lit),
not a piano that misses everything: recording pauses until an echo comes back.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

LOG = logging.getLogger("pianod")

WINDOW = 0.40                  # the slowest strike counted (soft notes take ~185 ms)
DEAF_AFTER = 20                # consecutive notes with no echo: nothing is listening
FLUSH_EVERY = 10.0             # seconds between writes
KEEP_DAYS = 60


class Observer:
    def __init__(self, root: str, calibration=None, clock=time.monotonic, wall=time.time):
        self.dir = os.path.join(root, "observe")
        self.cal = calibration
        self.clock, self.wall = clock, wall
        self.lock = threading.Lock()
        self.pending: dict[int, list] = {}       # key -> [rows waiting for their echo]
        self.rows: list[list] = []               # resolved, not yet written
        self.source = "library"                  # set by the daemon: library, take, live, ...
        self.enabled = True                      # off during a calibration (it measures itself)
        self.misses: list[list] = []             # misses in a row, held until an echo vouches
        self.deaf = False                        # for them (or DEAF_AFTER says nobody listens)
        self.counts = {"notes": 0, "sounded": 0, "missed": 0}
        self.last_flush = clock()

    # --- from MidiOut -------------------------------------------------------------------------
    def sent(self, key: int, velocity: int, pedal: bool, sounding: int) -> None:
        if not self.enabled:
            return
        now = self.clock()
        pred = None
        if self.cal is not None:
            pred = round(self.cal.l_on(key, velocity, pedal) * 1000, 1)
        row = [round(self.wall(), 3), key, velocity, int(pedal), sounding, None, None, None,
               pred, self.source, now]               # the last goes before writing
        with self.lock:
            self.pending.setdefault(key, []).append(row)

    def released(self, key: int) -> None:
        """A note-off sent: if it beat the echo, the hold was too short to land (likely)."""
        now = self.clock()
        with self.lock:
            for row in self.pending.get(key, ()):
                if row[7] is None:
                    row[7] = round((now - row[10]) * 1000, 1)

    # --- from MIDI in -------------------------------------------------------------------------
    def echo(self, key: int, velocity: int) -> bool:
        """A note-on from the key sensors. True if it was one of ours."""
        now = self.clock()
        with self.lock:
            rows = self.pending.get(key)
            if not rows or now - rows[0][10] > WINDOW:
                return False
            row = rows.pop(0)
            row[5] = round((now - row[10]) * 1000, 1)
            row[6] = velocity
            if self.deaf:
                LOG.info("observe: echoes again; recording")
            self.deaf = False
            for miss in self.misses:
                self._keep(miss)
            self.misses = []
            self._keep(row)
            return True

    # --- housekeeping, from the daemon's loop ---------------------------------------------------
    def poll(self) -> None:
        now = self.clock()
        with self.lock:
            for key, rows in list(self.pending.items()):
                while rows and now - rows[0][10] > WINDOW:
                    row = rows.pop(0)
                    if self.deaf:
                        continue
                    self.misses.append(row)
                    if len(self.misses) >= DEAF_AFTER:
                        LOG.info("observe: %d notes with no echo; not recording until one comes",
                                 DEAF_AFTER)
                        self.deaf, self.misses = True, []
                if not rows:
                    del self.pending[key]
            if self.rows and now - self.last_flush >= FLUSH_EVERY:
                self._flush()

    def _keep(self, row: list) -> None:
        self.counts["notes"] += 1
        self.counts["sounded" if row[5] is not None else "missed"] += 1
        self.rows.append(row[:10])

    def flush(self) -> None:
        with self.lock:
            self._flush()

    def _flush(self) -> None:
        self.last_flush = self.clock()
        if not self.rows:
            return
        rows, self.rows = self.rows, []
        os.makedirs(self.dir, exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.localtime(self.wall()))
        try:
            with open(os.path.join(self.dir, f"{day}.jsonl"), "a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
        except OSError as e:
            LOG.warning("observe: can't write (%s)", e)
            return
        self._expire()

    def _expire(self) -> None:
        cutoff = time.strftime("%Y-%m-%d", time.localtime(self.wall() - KEEP_DAYS * 86400))
        for name in os.listdir(self.dir):
            if name.endswith(".jsonl") and name < cutoff:
                try:
                    os.unlink(os.path.join(self.dir, name))
                except OSError:
                    pass

    def status(self) -> dict:
        with self.lock:
            return {**self.counts, "listening": not self.deaf}


# --- reading it back ---------------------------------------------------------------------------
NAMES = ("A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#")
BANDS = ((0, 20), (20, 35), (35, 55), (55, 80), (80, 128))     # velocity sent


def key_name(k: int) -> str:
    return f"{NAMES[(k - 21) % 12]}{(k - 12) // 12}"


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def read(root: str, days: int = 7, wall=time.time) -> list[list]:
    d = os.path.join(root, "observe")
    since = time.strftime("%Y-%m-%d", time.localtime(wall() - (days - 1) * 86400))
    rows = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else ():
        if name.endswith(".jsonl") and name[:-6] >= since:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    return rows


def summary(rows: list[list], min_n: int = 20) -> list[str]:
    """What the music says about the calibration: misses, and strikes early or late of it."""
    if not rows:
        return ["nothing observed yet"]
    out = []
    hit = [r for r in rows if r[5] is not None]
    out.append(f"{len(rows)} notes, {len(rows) - len(hit)} never sounded "
               f"({100 * (len(rows) - len(hit)) / len(rows):.1f}%)")
    by_src: dict[str, int] = {}
    for r in rows:
        by_src[r[9]] = by_src.get(r[9], 0) + 1
    out.append("from " + ", ".join(f"{k} {v}" for k, v in sorted(by_src.items())))
    out.append("velocity  notes  missed  strike ms (median)  vs calibration")
    for lo, hi in BANDS:
        band = [r for r in rows if lo <= r[2] < hi]
        if not band:
            continue
        b_hit = [r for r in band if r[5] is not None]
        miss = 100 * (len(band) - len(b_hit)) / len(band)
        strike = _median([r[5] for r in b_hit])
        err = _median([r[5] - r[8] for r in b_hit if r[8] is not None])
        out.append(f"{lo:3}-{hi - 1:<5} {len(band):6}  {miss:5.1f}%  "
                   f"{'' if strike is None else f'{strike:6.0f}':>18}  "
                   f"{'' if err is None else f'{err:+6.0f}':>14}")
    for label, test in (("pedal down", lambda r: r[3]), ("in chords (3+ sounding)", lambda r: r[4] >= 3)):
        sub = [r for r in rows if test(r)]
        if sub:
            m = 100 * sum(r[5] is None for r in sub) / len(sub)
            out.append(f"{label}: {len(sub)} notes, {m:.1f}% missed")
    keys: dict[int, list] = {}
    for r in rows:
        keys.setdefault(r[1], []).append(r)
    drift = []
    for k, rs in keys.items():
        errs = [r[5] - r[8] for r in rs if r[5] is not None and r[8] is not None]
        if len(errs) >= min_n:
            drift.append((abs(_median(errs)), _median(errs), k, len(errs)))
    drift.sort(reverse=True)
    if drift:
        out.append("furthest from the calibration: " + ", ".join(
            f"{key_name(k)} {e:+.0f} ms ({n})" for _, e, k, n in drift[:6]))
    worst = sorted(((sum(r[5] is None for r in rs) / len(rs), k, len(rs)) for k, rs in keys.items()
                    if len(rs) >= min_n), reverse=True)
    worst = [w for w in worst if w[0] > 0][:6]
    if worst:
        out.append("most misses: " + ", ".join(f"{key_name(k)} {100 * m:.0f}% ({n})" for m, k, n in worst))
    return out
