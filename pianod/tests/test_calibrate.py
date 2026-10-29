import json

from pianod import calibration
from pianod.calibrate import (MARGIN, Calibrator, Cancelled, Interrupted, PreflightFailed,
                              Strike, bisect, compare, key_params, loudness_suspects)
from pianod.calibration import Calibration


class FakePiano:
    """A simulated piano with known answers. Key 48 is the weak one: it needs longer holds and
    its sensor reads low (while the mic hears it like its neighbours)."""

    def __init__(self, interrupt_at=None, fail_preflight=None, cancel_in_delay=False):
        self.interrupt_at = interrupt_at
        self.fail_preflight = fail_preflight
        self.cancel_in_delay = cancel_in_delay
        self.waited_idle = 0
        self.strikes = 0

    def md_true(self, k, v):
        base = 0.140 - v * 0.0015
        return base + (0.04 if k == 48 else 0.0)

    def sensor(self, k, v):
        return round(0.68 * v + 23) - (12 if k == 48 else 0)

    def sleep(self, seconds):
        if self.cancel_in_delay:
            raise Cancelled()

    def preflight(self):
        if self.fail_preflight:
            raise PreflightFailed(self.fail_preflight)
        return {"mic": "fake"}

    def strike(self, k, v, hold, listen=True):
        self.strikes += 1
        if self.interrupt_at == (k, self.strikes):
            self.interrupt_at = None
            raise Interrupted("C4")
        if hold < self.md_true(k, v):
            return Strike()
        s = self.sensor(k, v)
        return Strike(sensed=s, l_on=0.2 - v * 0.001, l_off=0.048,
                      db=(10 + 0.5 * (s + (12 if k == 48 else 0))) if listen else None)

    def restrike(self, k, v, hold, gap):
        return gap >= 0.06

    def wait_idle(self, seconds):
        self.waited_idle += 1


def run(piano, keys=(47, 48, 49, 50, 51), **kw):
    c = Calibrator(piano, keys=keys, **kw)
    c.run()
    return c


def test_bisect_finds_the_threshold():
    assert abs(bisect(lambda x: x >= 0.1234, 0.0, 0.25, steps=10) - 0.1234) < 0.001
    assert bisect(lambda x: False, 0.0, 0.25) is None


def test_a_full_run_recovers_the_simulated_piano():
    c = run(FakePiano())
    assert c.phase == "done" and c.done == 5
    p = c.params[50]
    for v, md in p["md"]:
        truth = FakePiano().md_true(50, v)
        assert truth <= md - MARGIN + 1e-9 < truth + 0.01
    assert abs(p["mr"] - MARGIN - 0.06) < 0.01
    assert p["l_off"] == 0.048
    # The velocity fit inverts the sensor: asking for what velocity 60 reads gives back ~60.
    cal = c.calibration()
    assert abs(cal.velocity(50, FakePiano().sensor(50, 60)) - 60) <= 1


def test_the_weak_key_gets_its_own_numbers():
    cal = run(FakePiano()).calibration()
    assert cal.md(48, 15) > cal.md(47, 15) + 0.03
    assert cal.velocity(48, 50) > cal.velocity(47, 50) + 10     # its sensor reads low


def test_a_sensor_reading_low_against_the_mic_is_flagged():
    c = run(FakePiano())
    assert any("check its sensor" in f for f in c.flags.get(48, []))
    assert 50 not in c.flags


def test_someone_playing_pauses_then_measures_that_key_again():
    piano = FakePiano(interrupt_at=(48, 40))       # mid-way through key 48
    c = run(piano)
    assert c.phase == "done" and piano.waited_idle == 1
    assert 48 in c.params and c.done == 5


def test_preflight_failure_is_reported():
    c = run(FakePiano(fail_preflight="set MIDIIN to REALTIME"))
    assert c.phase == "failed" and "REALTIME" in c.message


def test_cancel_during_the_delay():
    c = run(FakePiano(cancel_in_delay=True), delay=600)
    assert c.phase == "cancelled" and not c.params


def test_status_counts_down_then_reports_progress():
    t = [1000.0]
    c = Calibrator(FakePiano(), keys=(60,), delay=600, clock=lambda: t[0])
    assert c.status()["phase"] == "waiting" and c.status()["starts_in"] == 600
    t[0] += 90
    assert c.status()["starts_in"] == 510


def test_key_params_flags_a_silent_key_and_keeps_defaults():
    p, flags = key_params({v: Strike() for v in (12, 20, 30)}, {15: None}, None)
    assert "sv" not in p
    assert any("no usable curve" in f for f in flags)
    assert any("never sounded" in f for f in flags)


def test_save_writes_the_calibration_keeps_the_old_and_compares(tmp_path):
    old = Calibration({str(k): {"mr": 0.2} for k in (47, 48, 49, 50, 51)})
    (tmp_path / "calibration.json").write_text(json.dumps(old.to_json()))
    c = run(FakePiano())
    summary = c.save(str(tmp_path), old)
    assert summary[0] == "5 keys calibrated"
    assert any("re-strike gap" in s for s in summary)            # 0.2 -> ~0.075
    assert list(tmp_path.glob("calibration-before-*.json"))
    assert list(tmp_path.glob("calibration-raw-*.json"))
    back = calibration.load(str(tmp_path / "calibration.json"))
    assert set(back.keys) == {47, 48, 49, 50, 51}
    assert back.info["mic"] == "fake"


def test_compare_is_quiet_when_nothing_moved():
    cal = run(FakePiano()).calibration()
    assert compare(cal, cal) == []


def test_loudness_suspects_needs_neighbours():
    raw = {60: {"curve": {30: {"sensed": 40, "db": 20}, 60: {"sensed": 60, "db": 30}}}}
    assert loudness_suspects(raw) == {}
