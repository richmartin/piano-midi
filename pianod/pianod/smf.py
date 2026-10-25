"""Standard MIDI files in and out, as a flat list of timed events.

An Event is what the piano cares about and nothing more: notes and the three pedals
(sustain 64, sostenuto 66, soft 67). Times are absolute seconds from the start.
`kind` is "on" (a=note, b=velocity), "off" (a=note) or "cc" (a=controller, b=value).
"""

from __future__ import annotations

from typing import NamedTuple

import mido

PEDALS = (64, 66, 67)
_TICKS_PER_BEAT = 480
_TEMPO = 500_000               # µs per beat: 120 bpm, the SMF default
_TICKS_PER_SECOND = _TICKS_PER_BEAT * 1_000_000 / _TEMPO


class Event(NamedTuple):
    kind: str                  # "on" | "off" | "cc"
    time: float                # seconds from the start
    a: int                     # note, or controller number
    b: int = 0                 # velocity, or controller value


def load(path: str) -> tuple[list[Event], float]:
    """Read an SMF: (events sorted by time, duration in seconds).

    mido merges the tracks and converts delta ticks to seconds, tempo changes included.
    A note_on with velocity 0 is a note off, as the format allows.
    """
    events: list[Event] = []
    t = 0.0
    for msg in mido.MidiFile(path):
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            events.append(Event("on", t, msg.note, msg.velocity))
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            events.append(Event("off", t, msg.note))
        elif msg.type == "control_change" and msg.control in PEDALS:
            events.append(Event("cc", t, msg.control, msg.value))
    events.sort(key=lambda e: e.time)
    return events, t


def save(path: str, events: list[Event], name: str = "") -> None:
    """Write events as a single-track SMF at the default tempo."""
    mid = mido.MidiFile(ticks_per_beat=_TICKS_PER_BEAT)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    if name:
        track.append(mido.MetaMessage("track_name", name=name, time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=_TEMPO, time=0))
    last_tick = 0
    for e in sorted(events, key=lambda ev: ev.time):
        tick = round(e.time * _TICKS_PER_SECOND)
        delta, last_tick = tick - last_tick, tick
        if e.kind == "on":
            track.append(mido.Message("note_on", note=e.a, velocity=max(1, min(127, e.b)), time=delta))
        elif e.kind == "off":
            track.append(mido.Message("note_off", note=e.a, velocity=0, time=delta))
        elif e.kind == "cc":
            track.append(mido.Message("control_change", control=e.a, value=min(127, e.b), time=delta))
        else:
            raise ValueError(f"unknown event kind {e.kind!r}")
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.save(path)
