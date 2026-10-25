import time

from pianod.midiio import CollectOut
from pianod.player import Cursor, Player, scale_velocity
from pianod.smf import Event


EVENTS = [Event("on", 0.0, 60, 100), Event("off", 0.2, 60),
          Event("on", 0.2, 62, 100), Event("off", 0.4, 62)]


def test_scale_velocity():
    assert scale_velocity(100, 11) == 100
    assert scale_velocity(100, 6) == 50
    assert scale_velocity(100, 1) == 0          # volume 1: the note is skipped
    assert scale_velocity(1, 2) == 1            # never rounded down to an accidental note-off
    assert scale_velocity(127, 11) == 127


class TestCursor:
    def test_due_hands_out_each_event_once(self):
        c = Cursor(EVENTS)
        assert c.due(0.0) == EVENTS[:1]
        assert c.due(0.0) == []
        assert c.due(0.3) == EVENTS[1:3]
        assert c.due(10) == EVENTS[3:]
        assert c.next_time() is None

    def test_seek(self):
        c = Cursor(EVENTS)
        c.due(10)
        c.seek(0.2)
        assert c.next_time() == 0.2
        assert [e.a for e in c.due(0.2)] == [60, 62]     # the off and the on at 0.2

    def test_seek_past_the_end(self):
        c = Cursor(EVENTS)
        c.seek(99)
        assert c.next_time() is None
        assert c.due(100) == []


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_plays_in_order_and_ends():
    out = CollectOut()
    p = Player(out)
    ended = []
    p.on_ended = lambda: ended.append(True)
    p.load(EVENTS, 0.4, "t")
    p.set_volume(11)
    p.play()
    assert wait_for(lambda: ended)
    assert out.sent == [("on", 60, 100), ("off", 60), ("on", 62, 100), ("off", 62)]
    assert not p.playing


def test_pause_silences_and_resumes_where_it_was():
    out = CollectOut()
    p = Player(out)
    p.set_volume(11)
    p.load([Event("on", 0.0, 60, 100), Event("off", 5.0, 60)], 5.0)
    p.play()
    assert wait_for(lambda: ("on", 60, 100) in out.sent)
    p.pause()
    # pause panics: pedals up, the sounding note off, all-notes-off
    assert ("off", 60) in out.sent
    assert ("cc", 123, 0) in out.sent
    assert 0 < p.position() < 1
    pos = p.position()
    time.sleep(0.05)
    assert p.position() == pos                   # paused: the playhead stands still


def test_volume_scales_notes():
    out = CollectOut()
    p = Player(out)
    p.set_volume(6)
    p.load([Event("on", 0.0, 60, 100), Event("off", 0.05, 60)], 0.05)
    p.play()
    assert wait_for(lambda: not p.playing)
    assert ("on", 60, 50) in out.sent


def test_volume_1_skips_notes_entirely():
    out = CollectOut()
    p = Player(out)
    p.set_volume(1)
    p.load([Event("on", 0.0, 60, 100), Event("off", 0.05, 60)], 0.05)
    p.play()
    assert wait_for(lambda: not p.playing)
    assert not any(s[0] == "on" for s in out.sent)


def test_volume_range_is_checked():
    p = Player(CollectOut())
    for bad in (0, 12, -3):
        try:
            p.set_volume(bad)
            assert False, "should have raised"
        except ValueError:
            pass


def test_play_after_the_end_starts_over():
    out = CollectOut()
    p = Player(out)
    p.load([Event("on", 0.0, 60, 100), Event("off", 0.05, 60)], 0.05)
    p.play()
    assert wait_for(lambda: not p.playing)
    n = len([s for s in out.sent if s[0] == "on"])
    p.play()
    assert wait_for(lambda: not p.playing)
    assert len([s for s in out.sent if s[0] == "on"]) == n + 1


def test_on_event_reports_note_ons():
    seen = []
    p = Player(CollectOut(), on_event=seen.append)
    p.load(EVENTS, 0.4)
    p.play()
    assert wait_for(lambda: not p.playing)
    assert seen == [60, 62]
