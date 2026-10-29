"""The daemon's command handling, with a captured MIDI out and a tiny real library on disk."""

import json
import time

import pytest

from pianod import daemon, smf
from pianod.midiio import CollectOut
from pianod.smf import Event


def tiny_piece(path, note):
    smf.save(path, [Event("on", 0.0, note, 100), Event("off", 0.02, note)])


@pytest.fixture
def d(tmp_path, monkeypatch):
    lib = {"composers": [{"name": "Bach, J.S.", "slug": "bach-js", "works": [
        {"id": "file-id-000001", "title": "Prelude", "url": "/midi-files/file-id-000001.mid"},
        {"id": "file-id-000002", "title": "Fugue", "url": "/midi-files/file-id-000002.mid"},
    ]}]}
    (tmp_path / "midi-files").mkdir()
    (tmp_path / "library.json").write_text(json.dumps(lib))
    tiny_piece(str(tmp_path / "midi-files" / "file-id-000001.mid"), 60)
    tiny_piece(str(tmp_path / "midi-files" / "file-id-000002.mid"), 62)
    monkeypatch.setattr(daemon, "DATA", str(tmp_path))
    return daemon.Pianod(out=CollectOut(), midi_in=None)


def ok(d, cmd, **kw):
    r = d.handle({"cmd": cmd, **kw})
    assert r["ok"], r.get("error")
    return r


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_status_shape(d):
    st = ok(d, "status")["state"]
    assert st["mode"] == "library"
    assert st["works"] == 2
    assert st["midi_out"] is True
    assert st["perf"]["state"] == "empty"


def test_unknown_command_fails_loudly(d):
    r = d.handle({"cmd": "frobnicate"})
    assert not r["ok"]
    assert "frobnicate" in r["error"]


def test_browse(d):
    r = ok(d, "composers")
    assert r["composers"] == [{"name": "Bach, J.S.", "slug": "bach-js", "works": 2}]
    r = ok(d, "works", slug="bach-js")
    assert [w["title"] for w in r["works"]] == ["Prelude", "Fugue"]
    r = d.handle({"cmd": "works", "slug": "nobody"})
    assert not r["ok"]


def test_long_lists_come_in_pages_the_badge_can_read():
    from pianod.daemon import PAGE_BYTES, page
    items = [{"id": f"file-id-{i:06}", "title": "A rather long title, Op. %d" % i} for i in range(300)]
    got, offset, pages = [], 0, 0
    while offset is not None:
        r = page({"offset": offset}, "works", items)
        assert len(json.dumps(r)) < 4096 and len(json.dumps(r)) < PAGE_BYTES + 100
        got += r["works"]
        offset = r["next"]
        pages += 1
    assert got == items and pages > 5
    assert page({}, "works", items) == {"works": items}          # the CLI: everything at once
    assert page({"offset": 0}, "works", []) == {"works": [], "next": None}


def test_play_composer_runs_the_queue_to_the_end(d):
    ok(d, "play", slug="bach-js")
    assert wait_for(lambda: not d.player.playing and d.index == 1)
    ons = [s for s in d.out.sent if s[0] == "on"]
    # A flat file fits to the middle of the piano (sensor 62), sent as 57 by the default curve.
    assert ons == [("on", 60, 57), ("on", 62, 57)]
    st = d.state()
    assert st["title"] == "Fugue"


def test_play_ids_and_queue(d):
    ok(d, "play", ids=["file-id-000002"])
    r = ok(d, "queue")
    assert r["queue"] == ["Fugue"]
    assert wait_for(lambda: not d.player.playing)


def test_play_needs_a_target(d):
    r = d.handle({"cmd": "play"})
    assert not r["ok"]
    assert "ids, slug or all" in r["error"]


def test_toggle_with_nothing_queued(d):
    r = d.handle({"cmd": "toggle"})
    assert not r["ok"]


def test_volume_is_validated_and_persists(d, tmp_path):
    ok(d, "volume", v=3)
    assert d.player.volume == 3
    assert not d.handle({"cmd": "volume", "v": 0})["ok"]
    assert not d.handle({"cmd": "volume", "v": 99})["ok"]
    # A fresh daemon picks the volume up again.
    d2 = daemon.Pianod(out=CollectOut(), midi_in=None)
    assert d2.player.volume == 3


def test_mode_switch_stops_playback(d):
    ok(d, "play", slug="bach-js")
    ok(d, "mode", mode="performance")
    assert not d.player.playing
    assert d.mode == "performance"
    assert not d.handle({"cmd": "mode", "mode": "disco"})["ok"]
    r = d.handle({"cmd": "play", "slug": "bach-js"})
    assert not r["ok"]                     # no library playback in performance mode


def test_perf_commands_need_performance_mode(d):
    r = d.handle({"cmd": "perf_record"})
    assert not r["ok"]
    assert "performance" in r["error"]


def test_perf_record_take_save(d, tmp_path):
    ok(d, "mode", mode="performance")
    ok(d, "perf_record")
    d.looper.note_in("on", 60, 90, now=d.looper._rec_start + 0.1)
    d.looper.note_in("off", 60, now=d.looper._rec_start + 0.3)
    ok(d, "perf_take")
    r = ok(d, "perf_save", title="Evening noodle")
    assert r["saved"] == "Evening noodle.mid"
    recs = ok(d, "recordings")["works"]
    assert recs[0]["title"] == "Evening noodle"
    # And a recording can be played back through the library queue.
    ok(d, "mode", mode="library")
    ok(d, "play", ids=[recs[0]["id"]])
    assert wait_for(lambda: not d.player.playing)
    # A take isn't fitted: sensor 90 as played, sent as 99 by the default curve, at volume 8.
    assert ("on", 60, 99) in d.out.sent


def test_perf_save_with_nothing_recorded(d):
    ok(d, "mode", mode="performance")
    r = d.handle({"cmd": "perf_save"})
    assert not r["ok"]


def test_panic_stops_and_silences(d):
    ok(d, "play", slug="bach-js")
    assert wait_for(lambda: any(s[0] == "on" for s in d.out.sent))
    ok(d, "panic")
    assert not d.player.playing
    assert ("cc", 123, 0) in d.out.sent


def test_calibrate_needs_a_working_mic(d):
    d.mic = {"ok": False, "why": "no mic plugged in"}
    r = d.handle({"cmd": "calibrate", "delay": 10})
    assert not r["ok"] and "no mic plugged in" in r["error"]
    assert d.calib is None


def test_calibrate_blocks_playback_and_fails_cleanly_with_no_piano(d, monkeypatch):
    from pianod import calibrate
    monkeypatch.setattr(calibrate.Mic, "find", classmethod(lambda cls: None))
    d.mic = {"ok": True, "name": "test"}
    ok(d, "calibrate")
    assert ok(d, "status")["state"]["calib"]["phase"] in ("preflight", "measuring")
    r = d.handle({"cmd": "play", "slug": "bach-js"})
    assert not r["ok"] and "calibrating" in r["error"]
    # Nothing answers on the key sensors, so preflight names the wagon settings.
    assert wait_for(lambda: d.calib.finished, timeout=5)
    c = ok(d, "status")["state"]["calib"]
    assert c["phase"] == "failed" and "MIDI button" in c["message"]
    ok(d, "play", slug="bach-js")                  # free again


def test_a_delayed_calibration_fails_before_the_countdown_not_after(d, monkeypatch):
    """Preflight runs at once: nobody goes out for ten minutes to come back to a failure."""
    from pianod import calibrate
    monkeypatch.setattr(calibrate.Mic, "find", classmethod(lambda cls: None))
    d.mic = {"ok": True, "name": "test"}
    ok(d, "calibrate", delay=10)
    assert wait_for(lambda: d.calib.finished, timeout=5)
    assert d.calib.phase == "failed" and d.calib.starts_at is None
    assert ok(d, "calibrate_summary")["lines"] == []
