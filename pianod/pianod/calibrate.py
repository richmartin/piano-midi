"""Calibrate: run the piano through every measurement the playback model needs, per key.

For each of the 88 keys (docs/design.md, "Calibrate"):

  curve     a held note at each of CURVE_V: what the key sensors report (-> sv, mv), how long
            the strike took (-> l_on per velocity) and the release (-> l_off), and how loud the
            mic heard it (a cross-check on the sensor)
  md        at each of MD_V, the shortest hold that still sounds, by bisection
  mr        the shortest gap after a note-off before the key can sound again, by bisection —
            with the sustain pedal down (the harder case: on F4, 30 ms works without the pedal
            and never with it), and only counted when MR_TRIES in a row sound

Then, once, on a few keys across the keyboard:

  chords    a note at each of CHORD_V alone, then with 1, 2 and 3 others at the same velocity:
            how much quieter the sensor reads it in company (-> chord, for every key). Soft
            notes in the Gnossienne's chords came out 12-17 weaker than alone, and 4% missed.

A note "sounded" when the key sensors report it — the sweep on 2026-10-29 found they report a
strike exactly when the hammer lands. The mic only cross-checks loudness: a key whose loudness
at a given sensor reading is far from its neighbours' is flagged as a suspect sensor.

The Calibrator is pure logic over a rig (strike / restrike / preflight / wait_idle), so tests
drive it with a simulated piano. MidiRig is the real one: MIDI out, the key sensors, a Mic.
Someone playing the keys mid-run raises Interrupted; the key is measured again once the keys
have been quiet for a while.
"""

from __future__ import annotations

import logging
import math
import os
import queue
import re
import shutil
import statistics
import subprocess
import threading
import time
from array import array
from collections import deque
from dataclasses import dataclass

from . import store
from .calibration import Calibration, L_CC

LOG = logging.getLogger("pianod")

KEYS = range(21, 109)
CURVE_V = (12, 20, 30, 45, 60, 80, 100, 127)
CURVE_HOLD = 0.40
MD_V = (15, 30, 60)
MD_RANGE = (0.010, 0.250)
MR_V, MR_HOLD, MR_RANGE, MR_TRIES, MR_STEPS = 30, 0.30, (0.0, 0.250), 3, 4
STEPS = 5                      # bisection steps: ~8 ms resolution over 250 ms
MARGIN = 0.015                 # added to md and mr: measured on the edge is not safe
MR_MARGIN = 0.030              # mr's edge is sharper: 38 ms on F4 lost a quarter of re-strikes
CHORD_KEYS = (36, 43, 48, 55, 60, 67, 72, 79)
CHORD_WITH = (7, 4, 12)        # the others, in semitones above: a fifth, a third, an octave
CHORD_V, CHORD_TRIES, CHORD_HOLD = (20, 30, 45, 60), 2, 0.40
CHORD_STRIKE_S = 1.3           # roughly, per chord or note: for the time estimate
SENSOR_FLOOR = 27              # the quietest any key reads (12 on C4): a note that missed
IDLE_AFTER_PLAYING = 20.0      # seconds of quiet keys before carrying on after an interruption
NAMES = "C C# D D# E F F# G G# A A# B".split()


def key_name(k: int) -> str:
    return f"{NAMES[k % 12]}{k // 12 - 1}"


class Interrupted(Exception):
    """Someone played the keys (or a pedal) during a measurement."""


class Cancelled(Exception):
    pass


class PreflightFailed(Exception):
    pass


@dataclass
class Strike:
    sensed: int | None = None          # velocity the key sensors reported, or None: no strike
    l_on: float | None = None          # note-on sent -> sensor note-on
    l_off: float | None = None         # note-off sent -> sensor note-off
    db: float | None = None            # mic peak above the room, if there is a mic


# --- the analysis (pure) ------------------------------------------------------------------
def bisect(sounds, lo: float, hi: float, steps: int = STEPS) -> float | None:
    """The least x in (lo, hi] with sounds(x), assuming it's monotone; None if hi doesn't."""
    if not sounds(hi):
        return None
    for _ in range(steps):
        mid = (lo + hi) / 2
        if sounds(mid):
            hi = mid
        else:
            lo = mid
    return hi


def fit_line(points):
    """Least squares y = a*x + b through (x, y); None if the x's don't vary."""
    if len(points) < 2:
        return None
    mx = statistics.fmean(x for x, _ in points)
    my = statistics.fmean(y for _, y in points)
    sxx = sum((x - mx) ** 2 for x, _ in points)
    if sxx == 0:
        return None
    a = sum((x - mx) * (y - my) for x, y in points) / sxx
    return a, my - a * mx


def key_params(curve: dict, md: dict, mr: float | None) -> tuple[dict, list[str]]:
    """One key's measurements -> its calibration parameters, and anything worth flagging."""
    p, flags = {}, []
    # Velocity: send = sv * sensed + mv, fitted where the sensor still moves — above the floor,
    # and below the top (on the DKW10, 127 plays no louder than 100).
    pts = []
    for v, s in sorted(curve.items()):
        if s.sensed and v >= 20 and (not pts or s.sensed > pts[-1][0]):
            pts.append((s.sensed, v))
    line = fit_line(pts)
    if line and line[0] > 0:
        p["sv"], p["mv"] = round(line[0], 4), round(line[1], 2)
    else:
        flags.append("velocity: no usable curve, defaults kept")
    l_on = [[v, round(s.l_on, 4)] for v, s in sorted(curve.items()) if s.l_on is not None]
    if l_on:
        p["l_on"] = l_on
    offs = [s.l_off for s in curve.values() if s.l_off is not None]
    if offs:
        p["l_off"] = round(statistics.median(offs), 4)
    silent = [v for v, s in sorted(curve.items()) if s.sensed is None]
    if silent:
        flags.append(f"silent at velocity {', '.join(map(str, silent))} (held {CURVE_HOLD:.1f} s)")
    p["md"] = []
    for v, h in sorted(md.items()):
        if h is None:
            flags.append(f"velocity {v} never sounded even held {MD_RANGE[1] * 1000:.0f} ms")
            h = MD_RANGE[1]
        p["md"].append([v, round(h + MARGIN, 4)])
    if mr is None:
        flags.append(f"a re-strike {MR_RANGE[1] * 1000:.0f} ms after release never sounded")
        p["mr"] = MR_RANGE[1] + MR_MARGIN
    else:
        p["mr"] = round(mr + MR_MARGIN, 4)
    return p, flags


def loudness_suspects(raw: dict, limit_db: float = 4.0) -> dict[int, str]:
    """Keys whose mic loudness at sensor 60 is far from their neighbours': suspect sensors."""
    at60 = {}
    for k, r in raw.items():
        line = fit_line([(s["sensed"], s["db"]) for s in r["curve"].values()
                         if s["sensed"] and s["db"] is not None])
        if line:
            at60[k] = line[0] * 60 + line[1]
    out = {}
    for k, db in at60.items():
        near = [at60[j] for j in range(k - 3, k + 4) if j != k and j in at60]
        if len(near) >= 2:
            diff = db - statistics.median(near)
            if abs(diff) > limit_db:
                out[k] = (f"{diff:+.1f} dB against its neighbours at the same sensor reading: "
                          "check its sensor")
    return out


def chord_losses(raw: dict) -> dict[int, list]:
    """The chord stage's strikes -> Calibration.chord: for each number of others, the mean
    loss at each velocity, against the median alone reading. A miss counts as falling to the
    floor, so the correction covers misses as well as weak strikes."""
    out = {}
    for n in range(1, len(CHORD_WITH) + 1):
        pts = []
        for v in CHORD_V:
            pairs = [(t["alone"], t["with"][n - 1]) for r in raw.values() for t in r.get(v, [])
                     if t["alone"]]
            if not pairs:
                continue
            losses = [a - (w if w else SENSOR_FLOOR) for a, w in pairs]
            pts.append([statistics.median(a for a, _ in pairs),
                        round(max(0.0, statistics.fmean(losses)), 1)])
        pts.sort()
        if pts:
            out[n] = pts
    return out


def chord_summary(chord: dict[int, list], raw: dict) -> list[str]:
    misses = sum(1 for r in raw.values() for ts in r.values() for t in ts for w in t["with"]
                 if not w)
    total = sum(len(t["with"]) for r in raw.values() for ts in r.values() for t in ts)
    lines = []
    for n, pts in chord.items():
        lines.append(f"chords: with {n} other{'s' if n > 1 else ''}, a note loses " +
                     ", ".join(f"{loss:.0f} at {a:.0f}" for a, loss in pts))
    if total:
        lines.append(f"chords: {misses} of {total} notes in company missed")
    return lines


def compare(old: Calibration, new: Calibration) -> list[str]:
    """Keys that moved noticeably since the last calibration: how a failing part shows up."""
    changes = []
    for k in KEYS:
        if k not in old.keys or k not in new.keys:
            continue
        what = []
        d = new.l_on(k, 30) - old.l_on(k, 30)
        if abs(d) > 0.020:
            what.append(f"strike {d * 1000:+.0f} ms")
        d = new.velocity(k, 60) - old.velocity(k, 60)
        if abs(d) > 8:
            what.append(f"needs velocity {d:+d} for the same loudness")
        d = new.md(k, 15) - old.md(k, 15)
        if abs(d) > 0.030:
            what.append(f"min hold {d * 1000:+.0f} ms")
        d = new.mr(k) - old.mr(k)
        if abs(d) > 0.030:
            what.append(f"re-strike gap {d * 1000:+.0f} ms")
        if what:
            changes.append(f"{key_name(k)}: {', '.join(what)}")
    return changes


# --- the run ---------------------------------------------------------------------------------
class Calibrator:
    def __init__(self, rig, keys=KEYS, delay: float = 0.0, clock=time.monotonic,
                 chord_keys=CHORD_KEYS):
        self.rig = rig
        self.keys = list(keys)
        self.chord_keys = list(chord_keys)
        self.delay = delay
        self.clock = clock
        self.phase = "preflight"
        self.starts_at: float | None = None
        self.message = ""
        self.key: int | None = None
        self.done = 0
        self.started = clock()
        self.measuring_since: float | None = None
        self.raw: dict[int, dict] = {}
        self.chord_raw: dict[int, dict] = {}
        self.chord: dict[int, list] = {}
        self.params: dict[int, dict] = {}
        self.flags: dict[int, list[str]] = {}
        self.info: dict = {}

    @property
    def finished(self) -> bool:
        return self.phase in ("done", "failed", "cancelled")

    def status(self) -> dict:
        s = {"phase": self.phase, "done": self.done, "of": len(self.keys),
             "key": key_name(self.key) if self.key is not None else None,
             "message": self.message,
             "flags": sum(len(f) for f in self.flags.values())}
        if self.phase == "waiting" and self.starts_at is not None:
            s["starts_in"] = max(0, round(self.starts_at - self.clock()))
        if self.measuring_since is not None and self.done and not self.finished:
            per_key = (self.clock() - self.measuring_since) / self.done
            chords = len(self.chord_keys) - len(self.chord_raw)
            s["eta"] = round(per_key * (len(self.keys) - self.done) + chords * len(CHORD_V)
                             * CHORD_TRIES * (len(CHORD_WITH) + 1) * CHORD_STRIKE_S)
        return s

    def run(self) -> None:
        try:
            # Preflight first, even with a delay: a wrong wagon setting or a deaf mic is found
            # while whoever started it is still in the room, not after they've gone out.
            info = self.rig.preflight()
            if self.delay:
                self.phase = "waiting"
                self.starts_at = self.clock() + self.delay
                self.rig.sleep(self.delay)
                self.phase = "preflight"
                info = self.rig.preflight()        # again: things change in ten minutes
            self.info = {**info, "started": time.strftime("%Y-%m-%d %H:%M")}
            self.phase = "measuring"
            self.measuring_since = self.clock()
            for k in self.keys:
                self.key = k
                self._retrying(lambda: self._measure(k))
                self.done += 1
            self.key = None
            for k in self.chord_keys:
                self.message = f"chords on {key_name(k)}"
                self._retrying(lambda: self._measure_chords(k))
            self.message = ""
            self.chord = chord_losses(self.chord_raw)
            for k, why in loudness_suspects(self.raw).items():
                self.flags.setdefault(k, []).append(why)
            self.info["finished"] = time.strftime("%Y-%m-%d %H:%M")
            self.phase = "done"
        except Cancelled:
            self.phase, self.message = "cancelled", "cancelled"
        except PreflightFailed as e:
            self.phase, self.message = "failed", str(e)
        except Exception as e:                  # a calibration must never take pianod down
            LOG.exception("calibration failed")
            self.phase, self.message = "failed", f"{type(e).__name__}: {e}"

    def _retrying(self, measure) -> None:
        """Measure; if someone plays meanwhile, wait for quiet and measure again."""
        message = self.message
        while True:
            try:
                return measure()
            except Interrupted as e:
                self.phase, self.message = "paused", f"someone is playing ({e}); waiting"
                self.rig.wait_idle(IDLE_AFTER_PLAYING)
                self.phase, self.message = "measuring", message

    def _measure_chords(self, k: int) -> None:
        """Per velocity, CHORD_TRIES rounds of: alone, then with 1, 2, 3 others."""
        out = {}
        for v in CHORD_V:
            out[v] = []
            for _ in range(CHORD_TRIES):
                alone = self.rig.chord([k], v, CHORD_HOLD)[k]
                with_ = [self.rig.chord([k] + [k + d for d in CHORD_WITH[:n]], v, CHORD_HOLD)[k]
                         for n in range(1, len(CHORD_WITH) + 1)]
                out[v].append({"alone": alone, "with": with_})
        self.chord_raw[k] = out

    def _measure(self, k: int) -> None:
        rig = self.rig
        curve = {v: rig.strike(k, v, CURVE_HOLD) for v in CURVE_V}
        md = {v: bisect(lambda h: rig.strike(k, v, h, listen=False).sensed is not None, *MD_RANGE)
              for v in MD_V}
        mr = bisect(lambda g: all(rig.restrike(k, MR_V, MR_HOLD, g) for _ in range(MR_TRIES)),
                    *MR_RANGE, steps=MR_STEPS)
        self.params[k], flags = key_params(curve, md, mr)
        if flags:
            self.flags[k] = flags
        self.raw[k] = {"curve": {v: s.__dict__ for v, s in curve.items()}, "md": md, "mr": mr}

    def calibration(self) -> Calibration:
        return Calibration(self.params, L_CC,
                           {**self.info, "flags": {key_name(k): f for k, f in self.flags.items()}},
                           self.chord)

    def save(self, data_dir: str, previous: Calibration) -> list[str]:
        """Write the new calibration (keeping the old), its raw data; return a summary."""
        cal = self.calibration()
        stamp = time.strftime("%Y%m%d-%H%M")
        current = os.path.join(data_dir, "calibration.json")
        if os.path.exists(current):
            shutil.copyfile(current, os.path.join(data_dir, f"calibration-before-{stamp}.json"))
        store.write_json(os.path.join(data_dir, f"calibration-raw-{stamp}.json"),
                         {**{str(k): v for k, v in self.raw.items()},
                          "chords": {str(k): v for k, v in self.chord_raw.items()}})
        store.write_json(current, cal.to_json())
        summary = [f"{len(self.params)} keys calibrated"]
        summary += chord_summary(self.chord, self.chord_raw)
        summary += [f"{key_name(k)}: {f}" for k, fs in self.flags.items() for f in fs]
        if previous.keys:
            changed = compare(previous, cal)
            summary += changed or ["nothing changed noticeably since last time"]
        return summary


# --- the real rig ----------------------------------------------------------------------------
class Mic:
    """The first capture device (or PIANO_MIC), read continuously through arecord as 20 ms
    loudness readings, each stamped with when it arrived."""

    RATE, CHUNK = 16000, 320

    def __init__(self, device: str, name: str, clock=time.monotonic):
        self.device, self.name, self.clock = device, name, clock
        self.levels: deque = deque(maxlen=3000)          # (arrival time, dBFS), last minute
        self.noise = -90.0
        self.proc = subprocess.Popen(
            ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(self.RATE), "-c", "1",
             "-t", "raw"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._read, daemon=True, name="mic").start()

    @classmethod
    def find(cls) -> "Mic | None":
        found = find_device()
        return cls(*found) if found else None

    def _read(self) -> None:
        n = self.CHUNK * 2
        while True:
            b = self.proc.stdout.read(n)
            if not b or len(b) < n:
                return
            a = array("h")
            a.frombytes(b)
            ms = sum(x * x for x in a) / len(a)
            self.levels.append((self.clock(), 10 * math.log10(ms / 32768 ** 2 + 1e-12)))

    def peak(self, t0: float, t1: float) -> float | None:
        """Loudest 20 ms between t0 and t1 (allowing for arecord's buffering)."""
        vals = [db for t, db in list(self.levels) if t0 <= t <= t1 + 0.15]
        return max(vals) if vals else None

    def now(self, window: float = 0.1) -> float:
        t = self.clock()
        vals = [db for ts, db in list(self.levels) if ts >= t - window]
        return max(vals) if vals else self.noise

    def close(self) -> None:
        self.proc.terminate()


def find_device() -> tuple[str, str] | None:
    """(ALSA device, name) of PIANO_MIC, or else the first capture device; None if none."""
    device = os.environ.get("PIANO_MIC", "")
    if device:
        return device, device
    try:
        listing = subprocess.run(["arecord", "-l"], capture_output=True, text=True,
                                 timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^card (\d+): \S+ \[([^\]]*)\], device (\d+)", listing, re.M)
    if not m:
        return None
    return f"plughw:{m.group(1)},{m.group(3)}", m.group(2)


def probe_mic() -> dict:
    """Is there a mic, and does it deliver sound? Half a second of it, for the panel's sake:
    {"ok": True, "name", "dbfs"} or {"ok": False, "why"}. Hearing the piano is preflight's job."""
    found = find_device()
    if not found:
        return {"ok": False, "why": "no mic plugged in"}
    device, name = found
    try:
        b = subprocess.run(["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", "16000",
                            "-c", "1", "-t", "raw", "-s", "8000"],
                           capture_output=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "name": name, "why": f"{name}: {e}"}
    if len(b) < 2000:
        return {"ok": False, "name": name, "why": f"{name} gives no sound"}
    a = array("h")
    a.frombytes(b[: len(b) // 2 * 2])
    if not any(a):
        return {"ok": False, "name": name, "why": f"{name} is silent (muted?)"}
    ms = sum(x * x for x in a) / len(a)
    return {"ok": True, "name": name, "dbfs": round(10 * math.log10(ms / 32768 ** 2), 1)}


class MidiRig:
    """Strikes keys through MidiOut and listens to the key sensors (fed by the daemon)."""

    def __init__(self, out, mic: Mic | None, clock=time.monotonic):
        self.out, self.mic, self.clock = out, mic, clock
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.last_off = 0.0

    def feed(self, kind: str, a: int, b: int) -> None:
        """Every event from the piano's MIDI OUT, raw (no echo gate) — from the daemon."""
        self.q.put((self.clock(), kind, a, b))

    def sleep(self, seconds: float) -> None:
        if self.cancel.wait(max(0.0, seconds)):
            raise Cancelled()

    def _collect(self, key, until: float, got: list, pedal: bool = False) -> None:
        """Gather this key's (or these keys') sensor events until `until`; anything else means
        someone's playing (but with `pedal`, sustain-pedal reports are ours: restrike holds it
        down)."""
        keys = {key} if isinstance(key, int) else set(key)
        while True:
            if self.cancel.is_set():
                raise Cancelled()
            left = until - self.clock()
            if left <= 0:
                return
            try:
                t, kind, a, b = self.q.get(timeout=min(left, 0.05))
            except queue.Empty:
                continue
            if pedal and kind == "cc" and a == 64:
                continue
            if kind == "cc" or a not in keys:
                raise Interrupted(f"{key_name(a)}" if kind != "cc" else "a pedal")
            got.append((t, kind, a, b))

    def _settle(self) -> None:
        """Let the last note's damper fall and the room go quiet; drop any stragglers."""
        self.sleep(max(0.0, self.last_off + 0.25 - self.clock()))
        if self.mic:
            end = self.clock() + 3.0
            while self.mic.now() > self.mic.noise + 6 and self.clock() < end:
                self.sleep(0.05)
        while True:
            try:
                t, kind, a, b = self.q.get_nowait()
            except queue.Empty:
                return
            if kind == "on":
                raise Interrupted(key_name(a))

    def strike(self, key: int, vel: int, hold: float, listen: bool = True) -> Strike:
        """One note held for `hold`; `listen` also measures its loudness on the mic."""
        self._settle()
        got: list = []
        t0 = self.clock()
        self.out.note_on(key, vel)
        self._collect(key, t0 + hold, got)
        self.out.note_off(key)
        t1 = self.last_off = self.clock()
        self._collect(key, max(t1 + 0.35, t0 + 0.45), got)
        ons = [(t, b) for t, kind, _, b in got if kind == "on"]
        if not ons:
            return Strike()
        offs = [t for t, kind, _, _ in got if kind == "off" and t >= ons[0][0]]
        s = Strike(sensed=ons[0][1], l_on=ons[0][0] - t0,
                   l_off=(offs[0] - t1) if offs and offs[0] >= t1 else None)
        if self.mic and listen:
            self.sleep(max(0.0, t0 + 0.75 - self.clock()))       # let the audio arrive
            peak = self.mic.peak(t0, t0 + 0.6)
            s.db = None if peak is None else round(peak - self.mic.noise, 1)
        return s

    def chord(self, keys: list[int], vel: int, hold: float) -> dict[int, int | None]:
        """These keys together, held for `hold`: what the sensors read for each (None: missed)."""
        self._settle()
        got: list = []
        t0 = self.clock()
        for k in keys:
            self.out.note_on(k, vel)
        self._collect(keys, t0 + hold, got)
        for k in keys:
            self.out.note_off(k)
        self.last_off = self.clock()
        self._collect(keys, max(self.last_off + 0.35, t0 + 0.45), got)
        sensed: dict[int, int | None] = {k: None for k in keys}
        for t, kind, a, b in got:
            if kind == "on" and sensed[a] is None:
                sensed[a] = b
        return sensed

    def restrike(self, key: int, vel: int, hold: float, gap: float) -> bool:
        """With the sustain pedal down: a note, released, then the same key `gap` later.
        Did the second one sound?"""
        self._settle()
        got: list = []
        self.out.cc(64, 127)
        self._collect(key, self.clock() + 0.1, got, pedal=True)
        t0 = self.clock()
        self.out.note_on(key, vel)
        self._collect(key, t0 + hold, got, pedal=True)
        self.out.note_off(key)
        t1 = self.clock()
        self._collect(key, t1 + gap, got, pedal=True)
        self.out.note_on(key, vel)
        t2 = self.clock()
        self._collect(key, t2 + hold, got, pedal=True)
        self.out.note_off(key)
        self._collect(key, self.clock() + 0.25, got, pedal=True)
        self.out.cc(64, 0)
        self.last_off = self.clock()
        self._collect(key, self.last_off + 0.15, got, pedal=True)
        return sum(1 for _, kind, _, _ in got if kind == "on") >= 2

    def wait_idle(self, seconds: float) -> None:
        quiet_since = self.clock()
        while self.clock() - quiet_since < seconds:
            try:
                self.q.get(timeout=0.1)
                quiet_since = self.clock()
            except queue.Empty:
                pass
            if self.cancel.is_set():
                raise Cancelled()

    def preflight(self) -> dict:
        info = {"mic": self.mic.name if self.mic else None}
        if self.mic:
            self.sleep(1.0)
            vals = [db for _, db in list(self.mic.levels)[-40:]]
            if not vals:
                raise PreflightFailed(f"the mic ({self.mic.name}) isn't delivering any sound")
            self.mic.noise = statistics.median(vals)
            info["room_dbfs"] = round(self.mic.noise, 1)
        s = self.strike(60, 70, 0.4)
        if s.sensed is None:
            raise PreflightFailed("no reply from the key sensors: is the wagon's MIDI button lit, "
                                  "MIDIOUT=KBD OUT (R), and the wagon-to-piano cable seated?")
        if s.l_on > 0.3:
            raise PreflightFailed(f"the piano answered after {s.l_on * 1000:.0f} ms: "
                                  "set the wagon's MIDIIN to REALTIME")
        if self.mic and (s.db is None or s.db < 10):
            raise PreflightFailed(f"the mic ({self.mic.name}) can barely hear the piano "
                                  f"({s.db} dB over the room): move it closer")
        info["preflight_ms"] = round(s.l_on * 1000)
        return info

    def close(self) -> None:
        if self.mic:
            self.mic.close()

