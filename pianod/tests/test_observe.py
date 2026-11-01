"""Every note played against what the key sensors saw."""

import json

from pianod import observe
from pianod.calibration import Calibration
from pianod.midiio import MidiOut


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def obs(tmp_path):
    c = Clock()
    o = observe.Observer(str(tmp_path), Calibration(), clock=c, wall=lambda: 1.7e9 + c.t)
    return o, c


def test_a_note_and_its_echo(tmp_path):
    o, c = obs(tmp_path)
    o.source = "library"
    o.sent(60, 70, False, 2)
    c.t += 0.071
    assert o.echo(60, 74)
    o.flush()
    (row,) = observe.read(str(tmp_path), wall=lambda: 1.7e9 + c.t)
    assert row[1:7] == [60, 70, 0, 2, 71.0, 74]
    assert row[7] is None                        # held past its strike
    assert row[8] == 66.0                        # what the calibration expected at 70
    assert row[9] == "library"


def test_a_miss_and_how_long_it_was_held(tmp_path):
    o, c = obs(tmp_path)
    o.sent(40, 15, True, 0)
    c.t += 0.06
    o.released(40)
    c.t += 0.5
    o.poll()
    o.sent(41, 60, False, 0)                     # an echo vouches that someone is listening
    c.t += 0.08
    o.echo(41, 60)
    o.flush()
    miss, hit = observe.read(str(tmp_path), wall=lambda: 1.7e9 + c.t)
    assert (miss[1], miss[3], miss[5], miss[7]) == (40, 1, None, 60.0)
    assert hit[1] == 41 and hit[5] == 80.0
    assert o.status()["missed"] == 1


def test_nothing_listening_is_not_a_piano_that_misses(tmp_path):
    o, c = obs(tmp_path)
    for k in range(30):                          # the wagon is off: no echoes at all
        o.sent(40 + k, 60, False, 0)
        c.t += 0.5
        o.poll()
    o.flush()
    assert observe.read(str(tmp_path), wall=lambda: 1.7e9 + c.t) == []
    assert not o.status()["listening"]
    o.sent(60, 60, False, 0)
    c.t += 0.07
    assert o.echo(60, 60)
    assert o.status()["listening"]


def test_someone_at_the_keys_is_not_an_echo(tmp_path):
    o, c = obs(tmp_path)
    assert not o.echo(60, 50)                    # nothing sent
    o.sent(60, 50, False, 0)
    c.t += 1.0
    assert not o.echo(60, 50)                    # far too late to be ours


def test_paused_during_a_calibration(tmp_path):
    o, c = obs(tmp_path)
    o.enabled = False
    o.sent(60, 50, False, 0)
    assert o.pending == {}


def test_midi_out_reports_sends_with_the_pedal(tmp_path):
    class Port:
        def send(self, msg):
            pass

    o, c = obs(tmp_path)
    out = MidiOut(Port())
    out.observer = o
    out.cc(64, 127)
    out.note_on(60, 50)
    out.note_on(64, 50)
    assert o.pending[64][0][3:5] == [1, 1]       # pedal down, one note already sounding
    out.note_off(60)
    assert o.pending[60][0][7] is not None


def test_old_days_are_dropped(tmp_path):
    o, c = obs(tmp_path)
    d = tmp_path / "observe"
    d.mkdir()
    (d / "2001-01-01.jsonl").write_text("[]\n")
    o.sent(60, 50, False, 0)
    c.t += 0.07
    o.echo(60, 50)
    o.flush()
    assert not (d / "2001-01-01.jsonl").exists()


def test_summary(tmp_path):
    rows = []
    for i in range(40):
        rows.append([0, 48, 15, 1, 3, None if i % 4 == 0 else 160.0, 33, None, 145.0, "library"])
        rows.append([0, 60, 70, 0, 0, 70.0, 75, None, 66.0, "live"])
    lines = observe.summary(rows)
    text = "\n".join(lines)
    assert "80 notes, 10 never sounded" in lines[0]
    assert "library 40, live 40" in text
    assert "C3 +15 ms" in text and "C3 25%" in text
    assert "pedal down: 40 notes, 25.0% missed" in text
    json.dumps(lines)


def test_summary_of_nothing():
    assert observe.summary([]) == ["nothing observed yet"]
