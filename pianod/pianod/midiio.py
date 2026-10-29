"""The MIDI ports: finding them, sending to the piano, and filtering its echo.

Everything that needs the real ALSA/USB-MIDI hardware lives here, behind small classes a test
can replace. mido (with the rtmidi backend) is only imported when a real port is opened, so the
rest of pianod runs and tests anywhere.
"""

from __future__ import annotations

import logging
import threading
import time

LOG = logging.getLogger("pianod")

class MidiOut:
    """Notes and pedals out to the piano. Thread-safe; tracks what is sounding for panic()."""

    def __init__(self, port=None):
        self.port = port                   # a mido output port, or None (not connected)
        self.lock = threading.Lock()
        self.active: set[int] = set()      # notes currently on
        self.dirty = False                 # anything sent since the last panic (a pedal may be down)

    @property
    def connected(self) -> bool:
        return self.port is not None

    def _send(self, msg) -> None:
        with self.lock:
            if self.port is None:
                return
            try:
                self.port.send(msg)
            except (OSError, RuntimeError) as e:
                LOG.warning("MIDI out failed (%s); dropping port", e)
                self.port = None

    def note_on(self, note: int, velocity: int) -> None:
        import mido
        self.active.add(note)
        self.dirty = True
        self._send(mido.Message("note_on", note=note, velocity=max(0, min(127, velocity))))

    def note_off(self, note: int) -> None:
        import mido
        self.active.discard(note)
        self._send(mido.Message("note_off", note=note, velocity=0))

    def cc(self, control: int, value: int) -> None:
        import mido
        self.dirty = True
        self._send(mido.Message("control_change", control=control, value=max(0, min(127, value))))

    def panic(self) -> None:
        """Silence: pedals up, every sounding note off, and All Notes Off for good measure.

        A no-op when nothing was sent since the last panic, so idle transport fiddling
        (loading a track, stopping twice) sends nothing to the piano.
        """
        if not self.dirty:
            return
        self.cc(64, 0)
        self.cc(66, 0)
        self.cc(67, 0)
        for note in sorted(self.active):
            self.note_off(note)
        self.cc(123, 0)
        self.active.clear()
        self.dirty = False


class CollectOut(MidiOut):
    """For tests: remembers every call instead of sending it."""

    def __init__(self):
        super().__init__(port=None)
        self.sent: list[tuple] = []

    @property
    def connected(self) -> bool:
        return True

    def note_on(self, note, velocity):
        self.active.add(note)
        self.dirty = True
        self.sent.append(("on", note, velocity))

    def note_off(self, note):
        self.active.discard(note)
        self.sent.append(("off", note))

    def cc(self, control, value):
        self.dirty = True
        self.sent.append(("cc", control, value))


class EchoGate:
    """Drops the piano's echo of what we just played to it.

    If solenoid playback re-appears on the piano's MIDI OUT (the key sensors see the keys move),
    recording during playback would capture every existing layer again. The gate remembers each
    note we sent recently; an incoming note-on matching one within the window is the piano's
    echo, not the player's fingers, and is dropped (with its note-off).

    window=0 disables the gate — the right setting if the piano turns out not to echo.
    """

    def __init__(self, window: float = 0.25, clock=time.monotonic):
        self.window = window
        self.clock = clock
        self.recent: dict[int, list[float]] = {}     # note -> send times
        self.open_echoes: set[int] = set()           # notes whose note-on we swallowed
        self.lock = threading.Lock()

    def sent(self, note: int, now: float | None = None) -> None:
        """Call for every note-on pianod sends to the piano."""
        if not self.window:
            return
        now = self.clock() if now is None else now
        with self.lock:
            times = self.recent.setdefault(note, [])
            times.append(now)
            cutoff = now - self.window
            while times and times[0] < cutoff:
                times.pop(0)

    def is_echo(self, kind: str, note: int, now: float | None = None) -> bool:
        """True if this incoming event is our own playback coming back at us."""
        if not self.window:
            return False
        now = self.clock() if now is None else now
        with self.lock:
            if kind == "off":
                # A note-off belongs to whichever note-on it closes.
                if note in self.open_echoes:
                    self.open_echoes.discard(note)
                    return True
                return False
            times = self.recent.get(note, [])
            while times and times[0] < now - self.window:
                times.pop(0)
            if times:
                times.pop(0)            # one echo per send
                self.open_echoes.add(note)
                return True
            return False


def open_ports(match_out: str = "", match_in: str = ""):
    """Open the piano's MIDI out and in ports: (MidiOut, input port or None).

    A port matches if the pattern appears in its name (case-insensitive); an empty pattern takes
    the first port that isn't a software thru port. Returns (MidiOut(None), None) when there is
    no hardware — or no rtmidi backend at all — so pianod runs, silently, anywhere.
    """
    import mido

    try:
        out_names, in_names = mido.get_output_names(), mido.get_input_names()
    except Exception as e:                       # no rtmidi backend (a laptop without the package)
        LOG.warning("no MIDI backend (%s); running without MIDI", e)
        return MidiOut(None), None

    def pick(names, pattern):
        for name in names:
            if "through" in name.lower() or "thru" in name.lower():
                continue
            if pattern.lower() in name.lower():
                return name
        return None

    out_name = pick(out_names, match_out)
    in_name = pick(in_names, match_in)
    out_port = in_port = None
    if out_name:
        try:
            out_port = mido.open_output(out_name)
            LOG.info("MIDI out: %s", out_name)
        except (OSError, RuntimeError) as e:
            LOG.warning("can't open MIDI out %s: %s", out_name, e)
    if in_name:
        try:
            in_port = mido.open_input(in_name)
            LOG.info("MIDI in: %s", in_name)
        except (OSError, RuntimeError) as e:
            LOG.warning("can't open MIDI in %s: %s", in_name, e)
    return MidiOut(out_port), in_port
