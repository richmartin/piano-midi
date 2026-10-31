"""Notes from outside on a shared clock: Saxy's Band through the playback model."""

import time

import pytest

from pianod import live
from pianod.calibration import Calibration
from pianod.midiio import CollectOut
from test_daemon import d, ok, wait_for  # noqa: F401 - the fixture


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def lv():
    clock = Clock()
    lv = live.Live(CollectOut(), Calibration(), clock=clock, wall=lambda: clock.t + 5e8, curfew="")
    lv.fake = clock
    return lv


def at(lv, t):
    """Advance the clock to t (seconds after the start) and let the worker catch up."""
    lv.fake.t = 1000.0 + t
    with lv.cond:
        lv.cond.notify_all()
    wait_for(lambda: not lv.pending or lv.pending[0].time > lv.fake.t)
    time.sleep(0.01)


def wall(lv, dt):
    return lv.wall() + dt


def test_notes_go_out_early_by_their_strike_time(lv):
    r = lv.add("saxy", [{"t": wall(lv, 1.0), "note": 60, "vel": 80, "dur": 0.5}])
    assert (r["accepted"], r["late"]) == (1, 0)
    assert r["horizon"] == pytest.approx(r["now"] + 0.25)
    at(lv, 0.8)
    assert lv.out.sent == []
    at(lv, 0.95)                                 # struck ~80 ms before it sounds
    assert [s[0] for s in lv.out.sent] == ["on"]
    at(lv, 1.6)
    assert [s[0] for s in lv.out.sent] == ["on", "off"]
    assert lv.status() is None                   # done: nothing pending or sounding


def test_late_notes_are_dropped_and_counted_never_played_late(lv):
    r = lv.add("saxy", [{"t": wall(lv, 0.05), "note": 60, "vel": 40, "dur": 0.3},
                        {"t": wall(lv, 1.0), "note": 62, "vel": 40, "dur": 0.3}])
    assert (r["accepted"], r["late"]) == (1, 1)
    at(lv, 2.0)
    assert [s[1] for s in lv.out.sent] == [62, 62]


def test_a_late_pedal_goes_at_once(lv):
    r = lv.add("saxy", [{"t": wall(lv, -0.01), "cc": 64, "value": 0}])
    assert r["late"] == 1
    at(lv, 0.0)
    assert lv.out.sent == [("cc", 64, 0)]


def test_velocity_is_capped_and_mapped_onto_the_piano(lv):
    lv.add("saxy", [{"t": wall(lv, 1.0), "note": 60, "vel": 127, "dur": 0.3},
                    {"t": wall(lv, 1.0), "note": 64, "vel": 100, "dur": 0.3},
                    {"t": wall(lv, 1.0), "note": 67, "vel": 1, "dur": 0.3}])
    at(lv, 2.0)
    v = {s[1]: s[2] for s in lv.out.sent if s[0] == "on"}
    assert v[60] == v[64]                        # above the cap is the cap
    assert v[67] < v[64]
    assert v[67] >= 12


def test_a_note_held_into_the_next_bar_comes_up_in_time_to_be_struck_again(lv):
    lv.add("saxy", [{"t": wall(lv, 1.0), "note": 60, "vel": 60, "dur": 1.0}])
    lv.add("saxy", [{"t": wall(lv, 2.0), "note": 60, "vel": 60, "dur": 0.5}])
    at(lv, 3.0)
    assert [s[0] for s in lv.out.sent] == ["on", "off", "on", "off"]


def test_cancel_silences_now(lv):
    lv.add("saxy", [{"t": wall(lv, 0.5), "cc": 64, "value": 127},
                    {"t": wall(lv, 0.5), "note": 60, "vel": 60, "dur": 2.0},
                    {"t": wall(lv, 1.5), "note": 62, "vel": 60, "dur": 2.0}])
    at(lv, 0.6)
    assert lv.status()["source"] == "saxy"
    lv.cancel("saxy")
    assert lv.out.sent[-2:] == [("off", 60), ("cc", 64, 0)]
    at(lv, 5.0)
    assert ("on", 62) not in [s[:2] for s in lv.out.sent]
    assert lv.status() is None


def test_dry_runs_are_counted_but_nothing_moves(lv):
    r = lv.add("saxy", [{"t": wall(lv, 1.0), "note": 60, "vel": 60, "dur": 0.5}], dry=True)
    assert (r["accepted"], r["dryRun"]) == (1, True)
    at(lv, 2.0)
    assert lv.out.sent == [] and lv.status() is None


def test_quiet_hours_allow_only_dry_runs(lv):
    lv.curfew = "00:00-23:59"
    with pytest.raises(ValueError, match="quiet hours"):
        lv.add("saxy", [{"t": wall(lv, 1.0), "note": 60, "vel": 60, "dur": 0.5}])
    assert lv.add("saxy", [], dry=True)["accepted"] == 0


def test_curfew_runs_over_midnight():
    noon = time.mktime((2026, 10, 30, 12, 0, 0, 0, 0, -1))
    late = time.mktime((2026, 10, 30, 23, 30, 0, 0, 0, -1))
    early = time.mktime((2026, 10, 31, 7, 0, 0, 0, 0, -1))
    assert not live.in_curfew("20:00-08:00", noon)
    assert live.in_curfew("20:00-08:00", late) and live.in_curfew("20:00-08:00", early)


@pytest.mark.parametrize("bad", [{"t": 0, "note": 60, "vel": 60, "dur": 1},       # not near now
                                 {"note": 60, "vel": 60, "dur": 1},
                                 {"t": None, "note": 20, "vel": 60, "dur": 1},
                                 {"t": None, "note": 60, "vel": 0, "dur": 1},
                                 {"t": None, "note": 60, "vel": 60, "dur": 0},
                                 {"t": None, "cc": 7, "value": 1}])
def test_bad_events_are_refused_whole(lv, bad):
    if bad.get("t", 0) is None:
        bad["t"] = wall(lv, 1.0)
    with pytest.raises((ValueError, KeyError)):
        lv.add("saxy", [{"t": wall(lv, 1.0), "note": 64, "vel": 60, "dur": 1}, bad])
    assert lv.pending == []


def test_one_source_at_a_time(lv):
    lv.add("saxy", [{"t": wall(lv, 1.0), "note": 60, "vel": 60, "dur": 0.5}])
    with pytest.raises(ValueError, match="saxy"):
        lv.add("other", [{"t": wall(lv, 1.0), "note": 62, "vel": 60, "dur": 0.5}])


# --- through the daemon ----------------------------------------------------------------------
def test_the_band_and_the_library_take_turns(d):  # noqa: F811
    now = d.live.now()
    r = ok(d, "live", source="saxy", events=[{"t": now + 5, "note": 60, "vel": 60, "dur": 1}])
    assert r["accepted"] == 1
    assert d.state()["live"]["source"] == "saxy"
    r = d.handle({"cmd": "play", "ids": ["file-id-000001"]})
    assert not r["ok"] and "saxy" in r["error"]
    ok(d, "stop")                                # the badge's stop stops the Band too
    assert d.state()["live"] is None
    ok(d, "play", ids=["file-id-000001"])
    r = d.handle({"cmd": "live", "source": "saxy", "events": []})
    assert not r["ok"] and "Prelude" in r["error"]
    assert ok(d, "live", source="saxy", events=[], dryRun=True)["dryRun"]


def test_a_tie_over_the_bar_line_is_left_alone(lv):
    # Struck on the and-of-four, held through the downbeat; the next bar never strikes it again.
    lv.add("saxy", [{"t": wall(lv, 1.5), "note": 60, "vel": 60, "dur": 1.0}])
    lv.add("saxy", [{"t": wall(lv, 2.0), "note": 64, "vel": 60, "dur": 0.5}])
    off = [p.time for p in lv.pending if p.kind == "off" and p.a == 60]
    assert off == [pytest.approx(lv.fake.t + 2.5 - lv.cal.l_off(60))]


def test_a_sounding_note_whose_off_was_lost_is_auto_released(lv):
    """The 2026-10-31 13:10 incident: pending drained to nothing, a note stayed sounding."""
    lv.add("saxy", [{"t": wall(lv, 1.0), "note": 60, "vel": 80, "dur": 0.5}])
    at(lv, 1.0)                                       # the on is out, the off still pending
    assert [s[0] for s in lv.out.sent] == ["on"]
    with lv.cond:                                     # lose the off, as the incident did
        lv.pending = [p for p in lv.pending if p.kind != "off"]
    at(lv, 1.0 + live.MAX_HOLD + live.GRACE + 1)
    wait_for(lambda: ("off", 60) in lv.out.sent)
    assert ("off", 60) in lv.out.sent
    assert lv.status() is None                        # released, drained, counts reset


def test_a_sounding_note_with_its_off_still_coming_is_left_alone(lv):
    lv.add("saxy", [{"t": wall(lv, 1.0), "note": 60, "vel": 80, "dur": 0.5}])
    at(lv, 1.0)
    with lv.cond:
        before = list(lv.out.sent)
        lv._sweep(lv.fake.t + live.MAX_HOLD + live.GRACE + 1)   # overdue by age, but its off is pending
    assert lv.out.sent == before


def test_pedals_left_down_with_nothing_more_coming_are_released(lv):
    lv.add("saxy", [{"t": wall(lv, 0.5), "cc": 64, "value": 127},
                    {"t": wall(lv, 1.0), "note": 60, "vel": 80, "dur": 0.5}])
    at(lv, 3.0)                                       # all sent; sustain is down, nothing pending
    assert not any(s == ("cc", 64, 0) for s in lv.out.sent)
    at(lv, 3.0 + live.MAX_HOLD + live.GRACE + 1)
    wait_for(lambda: ("cc", 64, 0) in lv.out.sent)
    assert ("cc", 64, 0) in lv.out.sent


def test_accepted_non_dry_batches_are_audited_dry_ones_not(lv, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="pianod.audit"):
        lv.add("saxy", [{"t": wall(lv, 1.0), "note": 60, "vel": 80, "dur": 0.5}], dry=True)
        assert not caplog.records
        lv.add("saxy", [{"t": wall(lv, 1.0), "note": 60, "vel": 80, "dur": 0.5}])
    audit = [r for r in caplog.records if r.name == "pianod.audit"]
    assert len(audit) == 1
    msg = audit[0].getMessage()
    assert "saxy" in msg and "1 accepted" in msg and "0 late" in msg
