"""Performance mode: a loop station for the piano.

Record what is played on the keys; play it back through the solenoids; layer more on top.
MIDI layers merge cleanly — no microphone, no feedback — and every layer sounds on real strings.

The flow (what the badge's buttons drive):

    record()        start recording (from empty, or into silence after a stop)
    close_loop()    end the first recording and loop it immediately  -> state "playing", looping
    finish_take()   end the first recording as a one-shot take       -> plays back once on play
    toggle_play()   play/stop what has been recorded
    toggle_overdub() while playing: record on top (each pass of the loop commits as one layer,
                    so undo() peels overdubs back a pass at a time)
    undo()/redo()   drop/restore the newest layer
    clear()         back to empty
    save(path)      merge the layers into one SMF

Every method takes an explicit `now` so tests drive it without threads or sleeps; the daemon
passes time.monotonic(). poll(now) sends whatever playback is due — call it every few
milliseconds. `latency` shifts playback early by the piano's measured MIDI-in-to-hammer delay,
so a recorded layer and live playing line up (measure it with the hardware probe; default 0).

A note held across the loop boundary is recorded as it was played: its note-off lands early in
the next pass. Good enough for v1; quantise later if it ever matters.
"""

from __future__ import annotations

import time

from .smf import Event


class LooperError(ValueError):
    pass


class Looper:
    def __init__(self, out, clock=time.monotonic, latency: float = 0.0, on_event=None):
        self.out = out                     # a midiio.MidiOut
        self.clock = clock
        self.latency = latency
        self.on_event = on_event           # called with each note-on sent (the echo gate)
        self.state = "empty"               # empty | recording | playing | stopped
        self.looping = True                # close_loop() -> True, finish_take() -> False
        self.length = 0.0
        self.layers: list[list[Event]] = []
        self.redo_stack: list[list[Event]] = []
        self.overdub = False
        self._rec: list[Event] = []        # the layer being recorded (first or overdub)
        self._rec_start = 0.0
        self._play_start = 0.0             # clock() at unwrapped position zero
        self._merged: list[Event] = []     # committed layers, sorted, for playback
        self._next = 0                     # index into _merged within the current cycle
        self._cycle = 0                    # which pass of the loop _next is in

    # --- recording ----------------------------------------------------------------------
    def record(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        if self.state not in ("empty", "stopped"):
            raise LooperError(f"can't record while {self.state}")
        if self.state == "stopped" and self.layers:
            raise LooperError("recorded already: play and overdub, or clear first")
        self.state = "recording"
        self._rec, self._rec_start = [], now

    def note_in(self, kind: str, a: int, b: int = 0, now: float | None = None) -> None:
        """An event from the piano's keys (the daemon has already dropped echoes)."""
        now = self.clock() if now is None else now
        if self.state == "recording":
            self._rec.append(Event(kind, now - self._rec_start, a, b))
        elif self.state == "playing" and self.overdub:
            self._rec.append(Event(kind, (now - self._play_start) % self.length, a, b))

    def close_loop(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        self._finish_first(now, looping=True)
        self._start_playback(now)

    def finish_take(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        self._finish_first(now, looping=False)
        self.state = "stopped"

    def _finish_first(self, now: float, looping: bool) -> None:
        if self.state != "recording":
            raise LooperError(f"not recording ({self.state})")
        length = now - self._rec_start
        if length <= 0 or not self._rec:
            self.state = "stopped" if self.layers else "empty"
            self._rec = []
            raise LooperError("nothing was played")
        self.looping = looping
        self.length = length
        self._commit(self._rec)
        self._rec = []

    def _commit(self, layer: list[Event]) -> None:
        self.layers.append(list(layer))
        self.redo_stack = []
        self._rebuild()

    def _rebuild(self) -> None:
        self._merged = sorted((e for layer in self.layers for e in layer), key=lambda e: e.time)

    # --- playback -----------------------------------------------------------------------
    def _start_playback(self, now: float) -> None:
        self.state = "playing"
        self.overdub = False
        self._play_start = now
        self._cycle, self._next = 0, 0

    def toggle_play(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        if self.state == "playing":
            self._stop(now)
        elif self.state == "stopped" and self.layers:
            self._start_playback(now)
        elif self.state == "empty":
            raise LooperError("nothing recorded yet")
        else:
            raise LooperError(f"can't play while {self.state}")

    def _stop(self, now: float) -> None:
        if self.overdub:
            self.toggle_overdub(now)
        self.state = "stopped"
        self.out.panic()

    def toggle_overdub(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        if self.state != "playing":
            raise LooperError("overdub only while playing")
        if self.overdub:
            self.overdub = False
            if self._rec:
                self._commit(self._rec)
            self._rec = []
        else:
            self.overdub = True
            self._rec = []

    def undo(self, now: float | None = None) -> None:
        if not self.layers:
            raise LooperError("nothing to undo")
        self.redo_stack.append(self.layers.pop())
        self._rebuild()
        if not self.layers:
            if self.state == "playing":
                self.out.panic()
            self.state = "stopped"
        elif self.state == "playing":
            self._resync(self.clock() if now is None else now)

    def redo(self, now: float | None = None) -> None:
        if not self.redo_stack:
            raise LooperError("nothing to redo")
        self.layers.append(self.redo_stack.pop())
        self._rebuild()
        if self.state == "playing":
            self._resync(self.clock() if now is None else now)

    def _resync(self, now: float) -> None:
        """The merged list changed under a running playback: silence what may now be orphaned
        note-ons, and point _next at the first event still to come in the current pass."""
        self.out.panic()
        target = now - self._play_start + self.latency
        self._cycle = int(target // self.length) if self.length else 0
        within = target - self._cycle * self.length
        self._next = 0
        while self._next < len(self._merged) and self._merged[self._next].time <= within:
            self._next += 1

    def clear(self) -> None:
        if self.state == "playing":
            self.out.panic()
        self.state = "empty"
        self.layers, self.redo_stack, self._rec = [], [], []
        self.length, self.overdub = 0.0, False
        self._merged, self._next, self._cycle = [], 0, 0

    # --- the clock tick -------------------------------------------------------------------
    def poll(self, now: float | None = None) -> None:
        """Send everything due. Playback runs `latency` early so it lands with live playing."""
        now = self.clock() if now is None else now
        if self.state != "playing":
            return
        target = now - self._play_start + self.latency       # unwrapped position to send up to
        if not self.looping and target >= self.length:
            self._drain(self.length)
            self._stop(now)
            return
        while True:
            base = self._cycle * self.length
            if self._next >= len(self._merged):
                if not self.looping or base + self.length > target:
                    break
                # The pass is over: fold in what overdub has gathered, start the next pass.
                if self.overdub and self._rec:
                    self.layers.append(self._rec)
                    self.redo_stack = []
                    self._rec = []
                    self._rebuild()
                self._cycle += 1
                self._next = 0
                continue
            e = self._merged[self._next]
            if base + e.time > target:
                break
            self._send(e)
            self._next += 1

    def _drain(self, until: float) -> None:
        while self._next < len(self._merged) and self._merged[self._next].time <= until:
            self._send(self._merged[self._next])
            self._next += 1

    def _send(self, e: Event) -> None:
        if e.kind == "on":
            self.out.note_on(e.a, e.b)
            if self.on_event:
                self.on_event(e.a)
        elif e.kind == "off":
            self.out.note_off(e.a)
        elif e.kind == "cc":
            self.out.cc(e.a, e.b)

    # --- odds and ends --------------------------------------------------------------------
    def position(self, now: float | None = None) -> float:
        now = self.clock() if now is None else now
        if self.state == "recording":
            return now - self._rec_start
        if self.state == "playing" and self.length:
            p = now - self._play_start
            return p % self.length if self.looping else min(p, self.length)
        return 0.0

    def merged_events(self) -> list[Event]:
        return list(self._merged)

    def status(self, now: float | None = None) -> dict:
        return {
            "state": self.state,
            "layers": len(self.layers),
            "can_redo": bool(self.redo_stack),
            "looping": self.looping,
            "length": round(self.length, 2),
            "pos": round(self.position(now), 2),
            "overdub": self.overdub,
        }
