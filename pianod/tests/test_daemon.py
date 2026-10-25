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


def test_play_composer_runs_the_queue_to_the_end(d):
    ok(d, "play", slug="bach-js")
    assert wait_for(lambda: not d.player.playing and d.index == 1)
    ons = [s for s in d.out.sent if s[0] == "on"]
    assert ons == [("on", 60, 70), ("on", 62, 70)]       # the default volume 8 scales 100 to 70
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
    assert ("on", 60, 63) in d.out.sent        # 90 scaled by the default volume 8


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
