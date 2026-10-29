"""What this piano needs, per key, to sound a note when and how loud it is meant to.

The playback model (docs/design.md): a piece's events say when each note should *sound* and how
loud, in the piano's own key-sensor units. render() turns them into what to *send*, per key k:

  velocity   ov = clamp(iv * sv(k) + mv(k))
  latency    the note-on goes l_on(ov, k) early, the note-off l_off(k) early, pedals l_cc early
  duration   the hold sent is at least md(ov, k), or the hammer is pulled back before it lands
  re-strike  a key needs mr(k) between its note-off and its next note-on to sound again

The result is in send-time order, and starts up to LEAD before the piece does. The numbers come
from calibration.json (written by a calibration run); without one, DEFAULTS — measured on this
piano's middle C and a sweep of all 88 keys, 2026-10-29 — apply to every key.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, NamedTuple

from .smf import Event

LOG = logging.getLogger("pianod")

LEAD = 0.25                    # the earliest anything is sent ahead of its sound time
_TINY = 0.002                  # the least gap between two messages for one key

DEFAULTS = {
    # sensor velocity wanted -> velocity to send: middle C's measured curve, fitted
    "sv": 1.466, "mv": -33.4,
    # (sent velocity, seconds): median strike times over all 88 keys, plus middle C at 70
    "l_on": [[15, 0.145], [30, 0.118], [60, 0.080], [70, 0.066]],
    "l_off": 0.048,
    # (sent velocity, seconds): the slowest key's shortest sounding hold, plus 20 ms
    "md": [[15, 0.150], [30, 0.120], [60, 0.060]],
    "mr": 0.100,               # not yet measured: a guess from the Gnossienne's re-strikes
}
L_CC = 0.048                   # pedals, not yet measured: assumed to move like the dampers
MIN_VELOCITY = 12              # every key sounded at 12 when held long enough


class Sent(NamedTuple):
    """An event to send. `time` is when to send it; `sound` when it is meant to be heard."""
    kind: str
    time: float
    a: int
    b: int = 0
    sound: float = 0.0


def _interp(points, x: float) -> float:
    """Piecewise linear through (x, y) points, flat beyond the ends."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (x - x0) * (y1 - y0) / (x1 - x0)
    return points[-1][1]


class Calibration:
    def __init__(self, keys: dict | None = None, l_cc: float = L_CC, info: dict | None = None):
        self.keys = {int(k): {**DEFAULTS, **v} for k, v in (keys or {}).items()}
        self.l_cc = l_cc
        self.info = info or {}             # when, which mic: whatever the run recorded

    def _p(self, key: int) -> dict:
        return self.keys.get(key, DEFAULTS)

    def velocity(self, key: int, iv: float) -> int:
        p = self._p(key)
        return max(MIN_VELOCITY, min(127, round(iv * p["sv"] + p["mv"])))

    def l_on(self, key: int, ov: int) -> float:
        return min(LEAD, _interp(self._p(key)["l_on"], ov))

    def l_off(self, key: int) -> float:
        return self._p(key)["l_off"]

    def md(self, key: int, ov: int) -> float:
        return _interp(self._p(key)["md"], ov)

    def mr(self, key: int) -> float:
        return self._p(key)["mr"]

    # --- the model ------------------------------------------------------------------------
    def render(self, events: list[Event], loudness: Callable[[int], float]) -> list[Sent]:
        """Sound-time events -> send-time events. `loudness` maps a file velocity to the
        sensor velocity wanted; 0 means don't play the note."""
        end = events[-1].time if events else 0.0
        notes: list[list] = []                       # [key, t_on, t_off, ov]
        open_: dict[int, list] = {}
        for e in events:
            if e.kind == "on":
                if e.a in open_:                     # re-struck without a note-off: end it here
                    open_.pop(e.a)[2] = e.time
                iv = loudness(e.b)
                note = [e.a, e.time, end, self.velocity(e.a, iv) if iv > 0 else 0]
                notes.append(note)
                open_[e.a] = note
            elif e.kind == "off" and e.a in open_:
                open_.pop(e.a)[2] = e.time

        out: list[Sent] = [Sent("cc", e.time - self.l_cc, e.a, e.b, e.time)
                           for e in events if e.kind == "cc"]
        by_key: dict[int, list] = {}
        for key, t_on, t_off, ov in notes:
            if not ov:
                continue
            s_on = t_on - self.l_on(key, ov)
            s_off = max(t_off - self.l_off(key), s_on + self.md(key, ov))
            by_key.setdefault(key, []).append([s_on, s_off, t_on, t_off, ov])
        for key, ns in by_key.items():
            for a, b in zip(ns, ns[1:]):
                b[0] = max(b[0], a[0] + 2 * _TINY)          # a loud note can't overtake a soft one
                # The next strike needs the key back up for mr first: shorten this note for it.
                a[1] = max(a[0] + _TINY, min(a[1], b[0] - self.mr(key)))
                a[1] = min(a[1], b[0] - _TINY)
            for s_on, s_off, t_on, t_off, ov in ns:
                out.append(Sent("on", s_on, key, ov, t_on))
                out.append(Sent("off", s_off, key, 0, t_off))
        # Send-time order; at the same instant, offs and pedals before ons.
        out.sort(key=lambda s: (s.time, s.kind == "on"))
        return out

    # --- the file -------------------------------------------------------------------------
    def to_json(self) -> dict:
        return {"version": 1, "info": self.info, "l_cc": self.l_cc,
                "keys": {str(k): v for k, v in sorted(self.keys.items())}}


def load(path: str) -> Calibration:
    """calibration.json, or the measured defaults if there isn't a usable one."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("version") != 1:
            raise ValueError(f"unknown version {d.get('version')!r}")
        cal = Calibration(d.get("keys", {}), d.get("l_cc", L_CC), d.get("info", {}))
        LOG.info("calibration: %s (%d keys)", path, len(cal.keys))
        return cal
    except FileNotFoundError:
        LOG.info("no calibration yet; using the measured defaults")
    except (OSError, ValueError, TypeError, AttributeError) as e:
        LOG.warning("calibration %s unusable (%s); using the measured defaults", path, e)
    return Calibration()


def file_loudness(events: list[Event], volume: int, fit: bool = True) -> Callable[[int], float]:
    """Stage one of the model: this file's velocities -> sensor velocity wanted, at this volume.

    The library's files come from everywhere (converted rolls, modern recordings), so their
    velocities mean nothing in particular. Fit the file's own middle spread (5th-95th percentile)
    onto a usable span of the piano, never stretching more than 2x, so a quiet file stops sinking
    onto the floor and a flat one plays at a moderate level. With fit=False (our own takes,
    already in sensor units) the velocities are used as they are. Volume 8 is that as is; volume
    moves loudness above the floor in proportion; volume 1 is silence.
    """
    FLOOR, CENTRE, SPAN, MAX_STRETCH = 33.0, 62.0, 45.0, 2.0
    vs = sorted(e.b for e in events if e.kind == "on")
    if not vs:
        return lambda v: 0.0
    lo, hi = vs[len(vs) * 5 // 100], vs[min(len(vs) - 1, len(vs) * 95 // 100)]
    stretch = min(MAX_STRETCH, SPAN / (hi - lo)) if hi > lo else 0.0
    mid = (lo + hi) / 2
    factor = (max(1, min(11, volume)) - 1) / 7

    def loudness(v: int) -> float:
        if factor == 0:
            return 0.0
        fitted = v if not fit else CENTRE + (v - mid) * stretch
        fitted = max(FLOOR, min(120.0, fitted))
        return max(1.0, FLOOR + (fitted - FLOOR) * factor)

    return loudness
