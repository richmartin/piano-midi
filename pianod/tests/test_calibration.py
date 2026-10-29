import json
import time

from pianod import calibration
from pianod.calibration import DEFAULTS, LEAD, Calibration, file_loudness
from pianod.looper import Looper
from pianod.midiio import CollectOut
from pianod.player import Player
from pianod.smf import Event

AS_IS = lambda v: v                                          # noqa: E731


def notes(*specs):
    """(key, on, off, velocity) -> sound-time events."""
    ev = []
    for key, on, off, vel in specs:
        ev += [Event("on", on, key, vel), Event("off", off, key)]
    return sorted(ev, key=lambda e: e.time)


def sent_for(rendered, key, kind):
    return [s for s in rendered if s.a == key and s.kind == kind]


class TestVelocity:
    def test_the_middle_c_curve_inverts(self):
        cal = Calibration()
        assert cal.velocity(60, 33) == 15                    # the measured floor
        assert abs(cal.velocity(60, 64) - 60) <= 1
        assert abs(cal.velocity(60, 91) - 100) <= 1

    def test_clamped_to_what_sounds(self):
        cal = Calibration()
        assert cal.velocity(60, 1) == calibration.MIN_VELOCITY
        assert cal.velocity(60, 500) == 127

    def test_per_key_parameters_override_defaults(self):
        cal = Calibration({"60": {"sv": 1.0, "mv": 10}})
        assert cal.velocity(60, 50) == 60
        assert cal.velocity(61, 50) == Calibration().velocity(61, 50)


class TestRender:
    def test_soft_notes_go_earlier_than_loud(self):
        cal = Calibration()
        r = cal.render(notes((60, 1.0, 2.0, 33), (64, 1.0, 2.0, 91)), AS_IS)
        soft, loud = sent_for(r, 60, "on")[0], sent_for(r, 64, "on")[0]
        assert soft.time < loud.time < 1.0
        assert abs(1.0 - soft.time - 0.145) < 0.001          # l_on at velocity 15
        assert soft.sound == loud.sound == 1.0

    def test_note_offs_go_early_by_the_damper_latency(self):
        r = Calibration().render(notes((60, 0.0, 2.0, 80)), AS_IS)
        assert abs(sent_for(r, 60, "off")[0].time - (2.0 - DEFAULTS["l_off"])) < 1e-9

    def test_a_grace_note_is_held_long_enough_to_sound(self):
        cal = Calibration()
        r = cal.render(notes((60, 1.0, 1.03, 33)), AS_IS)    # 30 ms, at the floor
        on, off = sent_for(r, 60, "on")[0], sent_for(r, 60, "off")[0]
        assert off.time - on.time >= cal.md(60, 15) - 1e-9

    def test_a_restrike_shortens_the_note_before_it(self):
        cal = Calibration()
        r = cal.render(notes((60, 1.0, 1.5, 64), (60, 1.52, 2.0, 64)), AS_IS)
        (on1, on2), (off1, _) = sent_for(r, 60, "on"), sent_for(r, 60, "off")
        assert off1.time <= on2.time - cal.mr(60) + 1e-9
        assert on1.time < off1.time < on2.time

    def test_a_loud_restrike_never_overtakes_the_soft_note_before_it(self):
        r = Calibration().render(notes((60, 1.0, 1.01, 33), (60, 1.02, 1.5, 120)), AS_IS)
        kinds = [s.kind for s in r if s.a == 60]
        assert kinds == ["on", "off", "on", "off"]

    def test_overlapping_notes_on_one_key_end_at_the_restrike(self):
        ev = [Event("on", 0.0, 60, 64), Event("on", 1.0, 60, 64),
              Event("off", 1.5, 60), Event("off", 2.0, 60)]
        r = Calibration().render(ev, AS_IS)
        assert [s.kind for s in r if s.a == 60] == ["on", "off", "on", "off"]

    def test_pedals_go_early_too(self):
        r = Calibration().render([Event("cc", 1.0, 64, 127)], AS_IS)
        assert r[0].kind == "cc" and r[0].time < 1.0 and r[0].sound == 1.0

    def test_silent_notes_are_dropped_with_their_offs(self):
        r = Calibration().render(notes((60, 0.0, 1.0, 50)), lambda v: 0)
        assert r == []

    def test_send_order_puts_offs_before_ons_at_the_same_instant(self):
        r = Calibration().render(notes((60, 0.0, 1.0, 64), (62, 0.0, 1.0, 64)), AS_IS)
        times = [s.time for s in r]
        assert times == sorted(times)

    def test_nothing_is_sent_more_than_lead_early(self):
        cal = Calibration({"60": {"l_on": [[0, 5.0]]}})
        r = cal.render(notes((60, 1.0, 2.0, 64)), AS_IS)
        assert sent_for(r, 60, "on")[0].time >= 1.0 - LEAD - 1e-9


class TestPedal:
    KEY = {"60": {"sv_p": 1.0, "mv_p": 5.0, "v_min_p": 30}}

    def test_a_note_under_the_pedal_uses_the_pedal_curve(self):
        cal = Calibration(self.KEY)
        ev = sorted(notes((60, 1, 1.5, 40), (60, 3, 3.5, 40))
                    + [Event("cc", 2, 64, 127), Event("cc", 4, 64, 0)], key=lambda e: e.time)
        on = [s.b for s in cal.render(ev, AS_IS) if s.kind == "on"]
        assert on == [cal.velocity(60, 40), max(cal.velocity(60, 40), 30, round(40 * 1.0 + 5))]
        assert on[1] > on[0]

    def test_whichever_asks_for_more(self):
        cal = Calibration(self.KEY)
        assert cal.velocity(60, 100, pedal=True) == cal.velocity(60, 100)
        assert cal.velocity(60, 1, pedal=True) == 30                     # its own floor

    def test_no_pedal_curve_no_change(self):
        cal = Calibration()
        assert cal.velocity(60, 40, pedal=True) == cal.velocity(60, 40)


class TestFileLoudness:
    def test_a_quiet_file_is_lifted_off_the_floor(self):
        ev = notes(*[(60, i, i + 0.5, 30 + i % 15) for i in range(40)])
        loud = file_loudness(ev, 8)
        assert loud(30) < loud(37) < loud(44)
        assert loud(30) > 33 + 5                             # not sunk onto the floor

    def test_a_flat_file_plays_at_the_middle(self):
        loud = file_loudness(notes((60, 0, 1, 100), (62, 1, 2, 100)), 8)
        assert loud(100) == 62

    def test_volume_scales_above_the_floor_and_1_is_silence(self):
        ev = notes((60, 0, 1, 100))
        assert file_loudness(ev, 1)(100) == 0
        assert file_loudness(ev, 4)(100) < file_loudness(ev, 8)(100) < file_loudness(ev, 11)(100)
        assert file_loudness(ev, 2)(100) > 33

    def test_takes_are_not_fitted(self):
        loud = file_loudness(notes((60, 0, 1, 90), (62, 1, 2, 90)), 8, fit=False)
        assert loud(90) == 90


class TestLoad:
    def test_missing_file_means_defaults(self, tmp_path):
        cal = calibration.load(str(tmp_path / "nope.json"))
        assert cal.keys == {}

    def test_garbage_means_defaults(self, tmp_path):
        p = tmp_path / "calibration.json"
        p.write_text("{not json")
        assert calibration.load(str(p)).keys == {}

    def test_round_trip(self, tmp_path):
        p = tmp_path / "calibration.json"
        cal = Calibration({"60": {"mr": 0.2}}, info={"mic": "test"})
        p.write_text(json.dumps(cal.to_json()))
        back = calibration.load(str(p))
        assert back.mr(60) == 0.2 and back.info == {"mic": "test"}


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


class TestPlayerWithCalibration:
    def test_plays_the_rendered_piece_after_a_lead_in(self):
        out = CollectOut()
        p = Player(out, calibration=Calibration())
        p.load(notes((60, 0.0, 0.03, 64), (64, 0.1, 0.4, 64)), 0.4)
        p.play()
        assert wait_for(lambda: not p.playing)
        # The 30 ms note is held for its minimum, released before the next is even sent.
        assert [s[:2] for s in out.sent] == [("on", 60), ("off", 60), ("on", 64), ("off", 64)]

    def test_volume_change_re_renders(self):
        out = CollectOut()
        p = Player(out, calibration=Calibration())
        p.load(notes((60, 0.0, 0.1, 40), (62, 0.1, 0.2, 80)), 0.2)
        p.set_volume(11)
        p.play()
        assert wait_for(lambda: not p.playing)
        loud = [s[2] for s in out.sent if s[0] == "on"]
        out2 = CollectOut()
        p2 = Player(out2, calibration=Calibration())
        p2.load(notes((60, 0.0, 0.1, 40), (62, 0.1, 0.2, 80)), 0.2)
        p2.play()
        assert wait_for(lambda: not p2.playing)
        assert all(a > b for a, b in zip(loud, [s[2] for s in out2.sent if s[0] == "on"]))


class TestLooperWithCalibration:
    def make(self):
        out = CollectOut()
        return Looper(out, clock=lambda: 0.0, calibration=Calibration()), out

    def test_a_note_at_the_loop_start_goes_out_at_once_on_the_first_pass(self):
        lp, out = self.make()
        lp.record(now=10.0)
        lp.note_in("on", 60, 64, now=10.0)
        lp.note_in("off", 60, now=10.5)
        lp.close_loop(now=12.0)
        assert ("on", 60, Calibration().velocity(60, 64)) in out.sent

    def test_and_at_the_end_of_the_pass_before_on_later_passes(self):
        lp, out = self.make()
        lp.record(now=10.0)
        lp.note_in("on", 60, 64, now=10.0)
        lp.note_in("off", 60, now=10.5)
        lp.close_loop(now=12.0)
        out.sent.clear()
        lp.poll(now=13.95)                                   # 0.05 before the second pass
        assert [s[:2] for s in out.sent] == [("off", 60), ("on", 60)]

    def test_saving_writes_what_was_played_not_what_was_sent(self):
        lp, _ = self.make()
        lp.record(now=10.0)
        lp.note_in("on", 60, 40, now=10.2)
        lp.note_in("off", 60, now=10.25)
        lp.finish_take(now=11.0)
        got = [(e.kind, round(e.time, 6), e.a, e.b) for e in lp.merged_events()]
        assert got == [("on", 0.2, 60, 40), ("off", 0.25, 60, 0)]
