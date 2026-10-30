"""Notes from outside, played on a shared clock: Saxy's Band, its pianist trading fours.

A client (the tablet running the Band) sends notes a bar or two ahead, each with the time it is
meant to *sound* on pianod's clock (wall time, seconds; the client finds its offset from
GET /api/clock). They go through the same playback model as the library (calibration.py):
velocity per key, sent early by the strike time, held long enough to land.

  add(source, events)    -> {accepted, late, now, horizon}; late notes are dropped, never played
                            late. A late pedal change goes at once: a dropped pedal-up would
                            leave the dampers off.
  cancel(source)         everything pending from that source, and its sounding notes, now

Velocities are MIDI 1-127, capped at CAP, onto the piano's usable span (sensor 33-91, the
calibration does the rest) and scaled by pianod's volume. Inside CURFEW nothing is played, only
dry runs: scheduled and counted, no solenoid moves.
"""

from __future__ import annotations

import bisect
import itertools
import os
import threading
import time
from dataclasses import dataclass, field

from .calibration import LEAD
from .smf import PEDALS, Event

CAP = int(os.environ.get("PIANO_LIVE_CAP", "100"))           # the loudest velocity taken, of 127
CURFEW = os.environ.get("PIANO_CURFEW", "")                  # "20:00-08:00"; "" = none
AHEAD = 60.0                   # the furthest ahead a note may be sent, seconds
MAX_EVENTS = 4000              # in one request
MAX_HOLD = 30.0                # the longest note, seconds
FLOOR, TOP = 33.0, 91.0        # sensor velocity: the piano's softest, and where 100 sent lands
TICK = 0.05


def in_curfew(spec: str, now: float | None = None) -> bool:
    """spec "HH:MM-HH:MM", local time; it may run over midnight."""
    if not spec:
        return False
    a, b = (int(h) * 60 + int(m) for h, m in (part.split(":") for part in spec.split("-")))
    t = time.localtime(now)
    m = t.tm_hour * 60 + t.tm_min
    return a <= m < b if a <= b else (m >= a or m < b)


@dataclass(order=True)
class _Out:
    time: float                                  # monotonic, when to send
    order: int                                   # at one instant: offs and pedals before ons
    seq: int
    kind: str = field(compare=False)
    a: int = field(compare=False)
    b: int = field(compare=False)
    source: str = field(compare=False)


class Live:
    def __init__(self, out, calibration, on_event=None, clock=time.monotonic, wall=time.time,
                 cap: int = CAP, curfew: str = CURFEW):
        self.out = out
        self.cal = calibration
        self.on_event = on_event                 # the echo gate, as for the player
        self.clock, self.wall = clock, wall
        self.cap, self.curfew = cap, curfew
        self.volume = 8
        self.cond = threading.Condition()
        self.pending: list[_Out] = []
        self.sounding: dict[int, str] = {}       # key -> source, its on sent and off not yet
        self.pedals: dict[str, set] = {}         # source -> pedals it has down (as sent)
        self.pedal_sound: dict[str, bool] = {}   # source -> sustain down at its last sound time
        self.counts = {"accepted": 0, "late": 0}
        self.source = None                       # who has notes pending or sounding
        self._seq = itertools.count()
        threading.Thread(target=self._run, daemon=True, name="live").start()

    def now(self) -> float:
        return self.wall()

    def active(self) -> bool:
        with self.cond:
            return bool(self.pending or self.sounding)

    def status(self) -> dict | None:
        with self.cond:
            if not (self.pending or self.sounding):
                return None
            return {"source": self.source, "pending": len(self.pending), **self.counts}

    def _loudness(self, v: int) -> float:
        factor = (max(1, min(11, self.volume)) - 1) / 7
        if factor == 0:
            return 0.0
        wanted = FLOOR + (min(v, self.cap) - 1) * (TOP - FLOOR) / 126
        return max(1.0, FLOOR + (wanted - FLOOR) * factor)

    # --- in ----------------------------------------------------------------------------------
    def add(self, source: str, events: list, dry: bool = False) -> dict:
        source = str(source or "unknown")[:40]
        if not isinstance(events, list) or len(events) > MAX_EVENTS:
            raise ValueError(f"events must be a list of at most {MAX_EVENTS}")
        if not dry and in_curfew(self.curfew):
            raise ValueError(f"quiet hours ({self.curfew}): dry runs only")
        wall, mono = self.wall(), self.clock()
        to_mono = mono - wall
        ev: list[Event] = []
        for e in events:
            if not isinstance(e, dict):
                raise ValueError("each event is an object")
            t = float(e["t"])
            if not wall - AHEAD < t < wall + AHEAD:
                raise ValueError(f"t={t} is not within {AHEAD:.0f} s of now ({wall:.3f})")
            if "cc" in e:
                cc, value = int(e["cc"]), int(e["value"])
                if cc not in PEDALS or not 0 <= value <= 127:
                    raise ValueError(f"pedals only ({', '.join(map(str, PEDALS))}), value 0-127")
                ev.append(Event("cc", t + to_mono, cc, value))
            else:
                note, vel, dur = int(e["note"]), int(e["vel"]), float(e["dur"])
                if not 21 <= note <= 108 or not 1 <= vel <= 127 or not 0 < dur <= MAX_HOLD:
                    raise ValueError(f"note 21-108, vel 1-127, dur 0-{MAX_HOLD:.0f} s: {e}")
                ev.append(Event("on", t + to_mono, note, vel))
                ev.append(Event("off", t + to_mono + dur, note))
        ev.sort(key=lambda x: (x.time, x.kind == "on"))
        with self.cond:
            if self.source not in (None, source) and (self.pending or self.sounding):
                raise ValueError(f"the piano is playing {self.source}'s notes")
            # The sustain pedal as this source last left it, so notes under it get the pedal curve.
            lead_in = [Event("cc", -1e9, 64, 127)] if self.pedal_sound.get(source) else []
            sent = [s for s in self.cal.render(lead_in + ev, self._loudness) if s.time > -1e8]
            now = self.clock()
            outs, late, accepted = [], 0, 0
            drop_off: dict[int, int] = {}            # key -> offs to drop (their on was late)
            for s in sent:
                if s.kind == "on":
                    if s.time < now:
                        late += 1
                        drop_off[s.a] = drop_off.get(s.a, 0) + 1
                        continue
                    accepted += 1
                elif s.kind == "off" and drop_off.get(s.a):
                    drop_off[s.a] -= 1
                    continue
                elif s.kind == "cc":
                    late, accepted = (late + 1, accepted) if s.time < now else (late, accepted + 1)
                outs.append(s)
            if not dry:
                for e in ev:
                    if e.kind == "cc" and e.a == 64:
                        self.pedal_sound[source] = e.b >= 64
                self.source = source
                self._merge(outs, source, now)
                self.counts["accepted"] += accepted
                self.counts["late"] += late
                self.cond.notify_all()
            return {"accepted": accepted, "late": late, "now": round(self.wall(), 4),
                    "horizon": round(self.wall() + LEAD, 4), "dryRun": dry}

    def _merge(self, outs, source, now):
        # A key still held from an earlier batch has to come up mr before this batch strikes it.
        first_on: dict[int, float] = {}
        for s in outs:
            if s.kind == "on" and s.a not in first_on:
                first_on[s.a] = s.time
        moved = False
        for p in self.pending:
            if p.kind == "off" and p.a in first_on and p.time > first_on[p.a] - self.cal.mr(p.a):
                p.time = max(now, first_on[p.a] - self.cal.mr(p.a))
                moved = True
        if moved:
            self.pending.sort()
        for s in outs:
            order = 1 if s.kind == "on" else 0
            bisect.insort(self.pending, _Out(max(s.time, now), order, next(self._seq),
                                             s.kind, s.a, s.b, source))

    def cancel(self, source: str | None = None) -> dict:
        """Everything pending from source (None: from anyone), and silence what it sounds."""
        with self.cond:
            keep = [p for p in self.pending if source is not None and p.source != source]
            n = len(self.pending) - len(keep)
            self.pending = keep
            for key, src in list(self.sounding.items()):
                if source is None or src == source:
                    self.out.note_off(key)
                    del self.sounding[key]
            for src, down in self.pedals.items():
                if source is None or src == source:
                    for cc in sorted(down):
                        self.out.cc(cc, 0)
                    down.clear()
            for src in list(self.pedal_sound):
                if source is None or src == source:
                    del self.pedal_sound[src]
            if not (self.pending or self.sounding):
                self.source = None
                self.counts = {"accepted": 0, "late": 0}
            self.cond.notify_all()
            return {"cancelled": n}

    # --- out ---------------------------------------------------------------------------------
    def _send(self, p: _Out) -> None:
        if p.kind == "on":
            self.out.note_on(p.a, p.b)
            self.sounding[p.a] = p.source
            if self.on_event:
                self.on_event(p.a)
        elif p.kind == "off":
            self.out.note_off(p.a)
            self.sounding.pop(p.a, None)
        else:
            self.out.cc(p.a, p.b)
            down = self.pedals.setdefault(p.source, set())
            (down.add if p.b >= 64 else down.discard)(p.a)

    def _run(self) -> None:
        while True:
            with self.cond:
                while not self.pending:
                    self.cond.wait()
                now = self.clock()
                while self.pending and self.pending[0].time <= now:
                    self._send(self.pending.pop(0))
                if not (self.pending or self.sounding):
                    self.source = None
                    self.counts = {"accepted": 0, "late": 0}
                if self.pending:
                    self.cond.wait(min(TICK, max(0.001, self.pending[0].time - self.clock())))
