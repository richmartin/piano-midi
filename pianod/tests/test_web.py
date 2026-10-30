"""pianod's HTTP API, against a real server on a free port and the daemon test's tiny library."""

import json
import urllib.error
import urllib.request

import pytest

from pianod import web
from test_daemon import d  # noqa: F401 - the fixture


@pytest.fixture
def base(d):  # noqa: F811
    srv = web.serve(d, d.lib.root, port=0)
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(base, cmd, **args):
    req = urllib.request.Request(f"{base}/api/{cmd}", data=json.dumps(args).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_state(base):
    with urllib.request.urlopen(f"{base}/api/state", timeout=5) as r:
        st = json.load(r)
    assert st["type"] == "state" and st["works"] == 2


def test_play_and_stop(base, d):  # noqa: F811
    assert post(base, "play", ids=["file-id-000001"]) == (200, {"ok": True})
    assert d.state()["title"] == "Prelude"
    assert post(base, "stop")[1]["ok"]


def test_a_failed_command_says_why(base):
    status, r = post(base, "play", ids=["file-id-999999"])
    assert status == 200 and not r["ok"] and "no such work" in r["error"]


def test_only_library_and_playback(base):
    for cmd in ("wifi_join", "calibrate", "mode", "log", "nonsense"):
        assert post(base, cmd)[0] == 404


def test_events_start_with_the_state(base):
    with urllib.request.urlopen(f"{base}/api/events", timeout=5) as r:
        assert r.headers["Content-Type"] == "text/event-stream"
        line = r.readline().decode()
    assert line.startswith("data: ") and json.loads(line[6:])["type"] == "state"


def test_no_static_files_unless_asked(base):
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"{base}/index.html", timeout=5)


def test_clock(base, d):  # noqa: F811
    with urllib.request.urlopen(f"{base}/api/clock", timeout=5) as r:
        assert abs(json.load(r)["t"] - d.live.now()) < 1


def test_events_and_cancel(base, d):  # noqa: F811
    t = d.live.now() + 5
    status, r = post(base, "events", source="saxy", events=[{"t": t, "note": 60, "vel": 60, "dur": 1}])
    assert status == 200 and r["ok"] and r["accepted"] == 1 and r["late"] == 0
    assert post(base, "events/cancel", source="saxy")[1] == {"ok": True, "cancelled": 2}


def test_saxy_may_call_from_the_browser(base):
    req = urllib.request.Request(f"{base}/api/events", method="OPTIONS",
                                 headers={"Origin": "https://saxy2.fam-mart.in"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 204
        assert r.headers["Access-Control-Allow-Origin"] == "https://saxy2.fam-mart.in"
        assert "Content-Type" in r.headers["Access-Control-Allow-Headers"]
    req = urllib.request.Request(f"{base}/api/clock", headers={"Origin": "https://elsewhere.example"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert "Access-Control-Allow-Origin" not in r.headers
