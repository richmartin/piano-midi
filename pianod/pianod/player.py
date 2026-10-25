"""Plays a loaded piece to the piano, on time.

The scheduling itself is a pure Cursor over the event list (tested without threads); the Player
wraps it in one worker thread with a monotonic clock. Timing is all local — the network is never
in the play path.

Volume is 1-11 as velocity scaling, matching the old browser player: 11 is the file as written,
1 is silence (the notes are skipped, not sent at velocity 0).
"""

from __future__ import annotations

import threading
import time

from .smf import Event

TICK = 0.05                    # longest the worker sleeps between looks at the clock


def scale_velocity(velocity: int, volume: int) -> int:
    """Volume 1-11 -> velocity; 0 means don't play the note at all."""
    factor = (max(1, min(11, volume)) - 1) / 10
    if factor == 0:
        return 0
    return max(1, min(127, round(velocity * factor)))


class Cursor:
    """A position in a sorted event list: hand over everything due, in order, exactly once."""

    def __init__(self, events: list[Event]):
        self.events = events
        self.i = 0

    def seek(self, t: float) -> None:
        self.i = 0
        while self.i < len(self.events) and self.events[self.i].time < t:
            self.i += 1

    def due(self, elapsed: float) -> list[Event]:
        start = self.i
        while self.i < len(self.events) and self.events[self.i].time <= elapsed:
            self.i += 1
        return self.events[start:self.i]

    def next_time(self) -> float | None:
        return self.events[self.i].time if self.i < len(self.events) else None


class Player:
    def __init__(self, out, clock=time.monotonic, on_event=None):
        self.out = out                      # a midiio.MidiOut
        self.clock = clock
        self.on_event = on_event            # called with each note-on sent (the echo gate)
        self.on_ended = None                # called (from the worker thread) when the piece ends
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.events: list[Event] = []
        self.duration = 0.0
        self.title = ""
        self.volume = 8
        self.playing = False
        self._pos = 0.0                     # the playhead while paused
        self._started = 0.0                 # clock() at (virtual) position zero while playing
        self._cursor = Cursor([])
        self._gen = 0                       # bumped by stop/load/seek so a stale worker exits
        threading.Thread(target=self._run, daemon=True, name="player").start()

    # --- state -------------------------------------------------------------------------
    def load(self, events: list[Event], duration: float, title: str = "") -> None:
        with self.lock:
            self._silence()
            self.events = events
            self.duration = duration
            self.title = title
            self.playing = False
            self._pos = 0.0
            self._cursor = Cursor(events)
            self._gen += 1
            self.cond.notify_all()

    def position(self) -> float:
        with self.lock:
            if self.playing:
                return min(self.duration, self.clock() - self._started)
            return self._pos

    def set_volume(self, volume: int) -> None:
        if not 1 <= volume <= 11:
            raise ValueError("volume must be 1-11")
        with self.lock:
            self.volume = volume

    # --- transport ---------------------------------------------------------------------
    def play(self) -> None:
        with self.lock:
            if self.playing or not self.events:
                return
            if self._pos >= self.duration:
                self._pos = 0.0
                self._cursor.seek(0.0)
            self._started = self.clock() - self._pos
            self.playing = True
            self.cond.notify_all()

    def pause(self) -> None:
        with self.lock:
            if not self.playing:
                return
            self._pos = min(self.duration, self.clock() - self._started)
            self.playing = False
            self._silence()
            self.cond.notify_all()

    def stop(self) -> None:
        with self.lock:
            self.playing = False
            self._pos = 0.0
            self._cursor.seek(0.0)
            self._gen += 1
            self._silence()
            self.cond.notify_all()

    def seek(self, t: float) -> None:
        with self.lock:
            t = max(0.0, min(self.duration, t))
            self._silence()
            self._pos = t
            self._cursor.seek(t)
            if self.playing:
                self._started = self.clock() - t
            self._gen += 1
            self.cond.notify_all()

    def _silence(self) -> None:
        self.out.panic()

    # --- the worker ----------------------------------------------------------------------
    def _send(self, e: Event) -> None:
        if e.kind == "on":
            v = scale_velocity(e.b, self.volume)
            if v:
                self.out.note_on(e.a, v)
                if self.on_event:
                    self.on_event(e.a)
        elif e.kind == "off":
            self.out.note_off(e.a)
        elif e.kind == "cc":
            self.out.cc(e.a, e.b)

    def _run(self) -> None:
        while True:
            with self.cond:
                while not self.playing:
                    self.cond.wait()
                gen = self._gen
                elapsed = self.clock() - self._started
                due = self._cursor.due(elapsed)
                nxt = self._cursor.next_time()
                for e in due:
                    self._send(e)
                if nxt is None and elapsed >= self.duration:
                    self.playing = False
                    self._pos = self.duration
                    ended = self.on_ended
                else:
                    wait = min(TICK, max(0.0, (nxt if nxt is not None else self.duration) - elapsed))
                    self.cond.wait(wait if wait > 0 else 0.001)
                    continue
            # The piece is over; tell the daemon outside the lock (it will likely load the next).
            if ended and gen == self._gen:
                ended()
