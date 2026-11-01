"""The librarian (regenerate_library.py): what a MIDI file says about itself."""

import pathlib
import sys

import mido

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from regenerate_library import embedded_names  # noqa: E402


def mk(path, *texts):
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name=texts[0], time=0))
    for t in texts[1:]:
        track.append(mido.MetaMessage("text", text=t, time=0))
    track.append(mido.Message("note_on", note=60, velocity=70, time=0))
    mid.save(str(path))
    return path


def test_a_file_that_names_itself(tmp_path):
    assert embedded_names(mk(tmp_path / "a.mid", "Clair De Lune", "Claude Debussy")) == \
        ("Claude Debussy", "Clair De Lune")
    assert embedded_names(mk(tmp_path / "b.mid", "Ondine: Maurice Ravel")) == ("Maurice Ravel", "Ondine")
    assert embedded_names(mk(tmp_path / "c.mid", "Albeniz Isaac (1860-1909)", "Tango in D")) == \
        ("Albeniz Isaac (1860-1909)", "Tango in D")
    assert embedded_names(mk(tmp_path / "d.mid", "Rhapsody in Blue", "by George Gershwin")) == \
        ("George Gershwin", "Rhapsody in Blue")


def test_noise_names_nobody(tmp_path):
    assert embedded_names(mk(tmp_path / "e.mid", "Piano High", "Robert Finley, piano")) == (None, None)
    (tmp_path / "f.mid").write_bytes(b"not midi")
    assert embedded_names(tmp_path / "f.mid") == (None, None)
