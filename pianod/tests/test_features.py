"""What a piece is like, from its notes."""

from pianod import catalogue, features
from pianod.smf import Event


def scale(tonic, steps, dur=0.5, vel=60):
    ev, t, p = [], 0.0, tonic
    for s in [0] + steps:
        p += s
        ev += [Event("on", t, p, vel), Event("off", t + dur, p)]
        t += dur
    return ev


def test_a_minor_scale_is_minor_and_a_fast_one_is_fast():
    slow = features.measure(scale(57, [2, 1, 2, 2, 1, 2, 2, -12, 7, -7, 3, 4, -7]))   # A minor
    assert slow["key"] == "A minor"
    fast = features.measure(scale(60, [2, 2, 1, 2, 2, 2, 1] * 4, dur=0.1))
    assert fast["npm"] > slow["npm"] * 4
    assert fast["low"] == 60 and fast["poly"] == 1


def test_chords_pedal_and_nothing():
    ev = [Event("cc", 0.0, 64, 127)]
    for t in (0.0, 1.0):
        ev += [Event("on", t + i * 0.005, p, 80) for i, p in enumerate((48, 52, 55))]
        ev += [Event("off", t + 0.9, p) for p in (48, 52, 55)]
    ev += [Event("cc", 1.0, 64, 0)]
    f = features.measure(sorted(ev, key=lambda e: e.time))
    assert f["poly"] == 3 and f["pedal"] == 0.53 and f["vel"] == 80     # down 1.0 s of 1.9
    assert features.measure([]) is None


def test_the_catalogue_keeps_the_stats():
    lib = {"composers": [{"name": "Chopin", "slug": "chopin", "works": [
        {"id": "file-id-000001", "title": "Nocturne", "url": "/midi-files/file-id-000001.mid",
         "stats": {"npm": 120}}]}]}
    out = catalogue.tidy(catalogue.tidy(lib))          # the librarian's tidy, then the Pi's
    assert out["composers"][0]["works"][0]["stats"] == {"npm": 120}
