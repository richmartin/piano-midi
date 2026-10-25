"""The looper is driven entirely with explicit clock values: no threads, no sleeps."""

import pytest

from pianod import smf
from pianod.looper import Looper, LooperError
from pianod.midiio import CollectOut


def make():
    out = CollectOut()
    lp = Looper(out, clock=lambda: 0.0)
    return lp, out


def record_two_notes(lp):
    """A 2-second first layer: C at 0.5, E at 1.5."""
    lp.record(now=10.0)
    lp.note_in("on", 60, 90, now=10.5)
    lp.note_in("off", 60, now=11.0)
    lp.note_in("on", 64, 80, now=11.5)
    lp.note_in("off", 64, now=12.0)


class TestRecordAndLoop:
    def test_close_loop_starts_playing(self):
        lp, out = make()
        record_two_notes(lp)
        lp.close_loop(now=12.0)
        assert lp.state == "playing"
        assert lp.looping
        assert lp.length == 2.0
        assert len(lp.layers) == 1

    def test_loop_replays_each_cycle(self):
        lp, out = make()
        record_two_notes(lp)
        lp.close_loop(now=12.0)
        lp.poll(now=12.6)                       # 0.6 into the first pass
        assert out.sent == [("on", 60, 90)]
        lp.poll(now=14.0)                       # end of pass 1
        lp.poll(now=14.6)                       # 0.6 into pass 2: C again
        ons = [s for s in out.sent if s[0] == "on"]
        assert ons == [("on", 60, 90), ("on", 64, 80), ("on", 60, 90)]

    def test_take_plays_once_and_stops(self):
        lp, out = make()
        record_two_notes(lp)
        lp.finish_take(now=12.0)
        assert lp.state == "stopped"
        assert not lp.looping
        lp.toggle_play(now=20.0)
        lp.poll(now=22.5)                       # past the end of the take
        assert lp.state == "stopped"
        ons = [s for s in out.sent if s[0] == "on"]
        assert ons == [("on", 60, 90), ("on", 64, 80)]

    def test_empty_recording_is_refused(self):
        lp, _ = make()
        lp.record(now=10.0)
        with pytest.raises(LooperError):
            lp.close_loop(now=12.0)
        assert lp.state == "empty"

    def test_cant_record_twice(self):
        lp, _ = make()
        lp.record(now=10.0)
        with pytest.raises(LooperError):
            lp.record(now=11.0)


class TestOverdub:
    def test_overdub_layer_sounds_from_the_next_pass(self):
        lp, out = make()
        record_two_notes(lp)
        lp.close_loop(now=12.0)
        lp.toggle_overdub(now=12.0)
        lp.poll(now=12.6)
        lp.note_in("on", 67, 70, now=12.8)       # G at position 0.8 of pass 1
        lp.note_in("off", 67, now=13.0)
        lp.poll(now=13.9)
        assert not any(s == ("on", 67, 70) for s in out.sent)   # not yet: still pass 1
        lp.poll(now=14.9)                        # 0.9 into pass 2
        assert ("on", 67, 70) in out.sent
        assert len(lp.layers) == 2               # the pass's overdub became a layer

    def test_overdub_off_commits_the_rest(self):
        lp, out = make()
        record_two_notes(lp)
        lp.close_loop(now=12.0)
        lp.toggle_overdub(now=12.0)
        lp.note_in("on", 67, 70, now=12.1)
        lp.note_in("off", 67, now=12.2)
        lp.toggle_overdub(now=12.3)
        assert not lp.overdub
        assert len(lp.layers) == 2

    def test_stop_mid_overdub_commits(self):
        lp, out = make()
        record_two_notes(lp)
        lp.close_loop(now=12.0)
        lp.toggle_overdub(now=12.0)
        lp.note_in("on", 67, 70, now=12.1)
        lp.note_in("off", 67, now=12.2)
        lp.toggle_play(now=12.5)                 # stop
        assert lp.state == "stopped"
        assert len(lp.layers) == 2


class TestUndoRedo:
    def test_undo_removes_the_newest_layer_and_redo_restores_it(self):
        lp, out = make()
        record_two_notes(lp)
        lp.close_loop(now=12.0)
        lp.toggle_overdub(now=12.0)
        lp.note_in("on", 67, 70, now=12.1)
        lp.note_in("off", 67, now=12.2)
        lp.toggle_overdub(now=12.3)
        lp.undo(now=12.4)
        assert len(lp.layers) == 1
        assert lp.status()["can_redo"]
        lp.redo(now=12.5)
        assert len(lp.layers) == 2

    def test_undo_of_the_last_layer_stops_and_silences(self):
        lp, out = make()
        record_two_notes(lp)
        lp.close_loop(now=12.0)
        lp.poll(now=12.6)
        lp.undo(now=12.7)
        assert lp.state == "stopped"
        assert ("cc", 123, 0) in out.sent        # panic went out

    def test_undo_with_nothing_recorded(self):
        lp, _ = make()
        with pytest.raises(LooperError):
            lp.undo()


class TestLatency:
    def test_playback_runs_early_by_the_latency(self):
        out = CollectOut()
        lp = Looper(out, clock=lambda: 0.0, latency=0.1)
        lp.record(now=10.0)
        lp.note_in("on", 60, 90, now=10.5)
        lp.note_in("off", 60, now=11.0)
        lp.close_loop(now=12.0)
        lp.poll(now=12.41)                       # 0.41 elapsed + 0.1 latency >= 0.5: send now
        assert ("on", 60, 90) in out.sent


class TestSave:
    def test_merged_layers_save_as_one_smf(self, tmp_path):
        lp, _ = make()
        record_two_notes(lp)
        lp.finish_take(now=12.0)
        path = str(tmp_path / "take.mid")
        smf.save(path, lp.merged_events(), "take")
        events, duration = smf.load(path)
        assert [(e.kind, e.a) for e in events] == [("on", 60), ("off", 60), ("on", 64), ("off", 64)]
        assert abs(events[0].time - 0.5) < 0.005


class TestClear:
    def test_clear_resets_everything(self):
        lp, out = make()
        record_two_notes(lp)
        lp.close_loop(now=12.0)
        lp.poll(now=12.6)                        # a note is sounding
        lp.clear()
        assert lp.state == "empty"
        assert lp.layers == []
        assert ("cc", 123, 0) in out.sent        # was playing: silenced
