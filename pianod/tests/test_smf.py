from pianod import smf
from pianod.smf import Event


def test_roundtrip(tmp_path):
    events = [
        Event("cc", 0.0, 64, 100),          # sustain down
        Event("on", 0.0, 60, 80),
        Event("off", 0.5, 60),
        Event("on", 0.5, 64, 127),
        Event("cc", 0.9, 64, 0),            # sustain up
        Event("off", 1.0, 64),
    ]
    path = str(tmp_path / "t.mid")
    smf.save(path, events, "roundtrip")
    back, duration = smf.load(path)
    assert [(e.kind, e.a, e.b) for e in back] == [(e.kind, e.a, e.b) for e in events]
    for orig, loaded in zip(events, back):
        assert abs(orig.time - loaded.time) < 0.005          # tick quantisation only
    assert abs(duration - 1.0) < 0.005


def test_note_on_velocity_zero_is_off(tmp_path):
    import mido
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("note_on", note=60, velocity=70, time=0))
    track.append(mido.Message("note_on", note=60, velocity=0, time=480))   # off, the other spelling
    path = str(tmp_path / "v0.mid")
    mid.save(path)
    events, _ = smf.load(path)
    assert [e.kind for e in events] == ["on", "off"]


def test_only_piano_ccs_survive(tmp_path):
    import mido
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("control_change", control=64, value=90, time=0))
    track.append(mido.Message("control_change", control=7, value=100, time=0))    # channel volume: dropped
    track.append(mido.Message("program_change", program=5, time=0))               # dropped
    path = str(tmp_path / "cc.mid")
    mid.save(path)
    events, _ = smf.load(path)
    assert [(e.kind, e.a) for e in events] == [("cc", 64)]


def test_real_library_file_loads():
    """One of the actual Disklavier files from test/: it must parse and look like music."""
    import glob
    import os
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    files = glob.glob(os.path.join(here, "test", "*.mid"))
    assert files, "no test MIDI files in the repo's test/ directory"
    events, duration = smf.load(files[0])
    assert len(events) > 100
    assert duration > 10
    assert any(e.kind == "on" for e in events)
    assert all(events[i].time <= events[i + 1].time for i in range(len(events) - 1))
