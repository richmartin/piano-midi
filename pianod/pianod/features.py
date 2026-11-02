"""What a piece is like, measured from its notes: for the companion to answer "the fastest, most
energetic piece" or "something dark and broody" from the music rather than from its title.

  npm       notes per minute (struck notes over the playing time)
  vel       median velocity (0-127; sources differ, so compare within one source with care)
  pitch     median pitch (MIDI; 60 = middle C), low, high: the register
  pedal     share of the playing time with the sustain pedal down
  poly      mean notes struck together (onsets within 30 ms counted as one chord)
  key       best Krumhansl-Schmuckler key ("C minor"), and keyfit: how clearly (correlation)
"""

from __future__ import annotations

from . import smf

NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
# Krumhansl-Kessler key profiles
MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
CHORD = 0.03


def _corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def key_of(weights: list[float]) -> tuple[str, float]:
    best = ("", -2.0)
    for tonic in range(12):
        rot = weights[tonic:] + weights[:tonic]
        for mode, prof in (("major", MAJOR), ("minor", MINOR)):
            r = _corr(rot, prof)
            if r > best[1]:
                best = (f"{NAMES[tonic]} {mode}", r)
    return best


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def measure(events: list) -> dict | None:
    ons = [e for e in events if e.kind == "on"]
    if not ons:
        return None
    start, end = ons[0].time, max(e.time for e in events)
    span = max(end - start, 1.0)
    # pitch-class weights by duration (a held note counts more than a grace note)
    weights = [0.0] * 12
    held: dict[int, float] = {}
    for e in events:
        if e.kind == "on":
            held[e.a] = e.time
        elif e.kind == "off" and e.a in held:
            weights[e.a % 12] += min(e.time - held.pop(e.a), 4.0)
    if not any(weights):
        for e in ons:
            weights[e.a % 12] += 1
    key, fit = key_of(weights)
    # sustain-down time
    down, since = 0.0, None
    for e in events:
        if e.kind == "cc" and e.a == 64:
            if e.b >= 64 and since is None:
                since = e.time
            elif e.b < 64 and since is not None:
                down += e.time - since
                since = None
    if since is not None:
        down += end - since
    chords, last = 0, None
    for e in ons:
        if last is None or e.time - last > CHORD:
            chords += 1
        last = e.time
    pitches = [e.a for e in ons]
    return {"npm": round(len(ons) * 60 / span), "notes": len(ons),
            "vel": _median([e.b for e in ons]),
            "pitch": _median(pitches), "low": min(pitches), "high": max(pitches),
            "pedal": round(min(down / span, 1.0), 2), "poly": round(len(ons) / max(chords, 1), 2),
            "key": key, "keyfit": round(fit, 2)}


def measure_file(path: str) -> dict | None:
    events, _ = smf.load(path)
    return measure(events)
