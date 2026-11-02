"""What was played, and how much of it."""

from pianod.history import IDLE, History


def st(id="file-id-000001", pos=0.0, playing=True, length=100.0):
    return {"mode": "library", "playing": playing, "id": id, "title": "T " + id, "composer": "C",
            "pos": pos, "len": length}


def test_a_listen_heard_to_the_end_then_another(tmp_path):
    h = History(str(tmp_path), wall=lambda: 1e9)
    for t in range(0, 100, 10):
        h.tick(st(pos=t + 0.5), t)
    h.tick(st(pos=98.0), 98)
    h.tick(st(id="file-id-000002", pos=1.0), 100)
    (cur, done) = h.read()
    assert cur["id"] == "file-id-000002" and cur["now"]
    assert done["id"] == "file-id-000001" and done["finished"] and done["heard"] == 98.0


def test_a_skip_a_restart_and_a_stop(tmp_path):
    h = History(str(tmp_path))
    h.tick(st(pos=30.0), 0)
    h.tick(st(pos=2.0), 1)                       # started again: a listen of its own
    h.tick(st(pos=12.0), 2)
    h.tick(st(pos=12.0, playing=False), 2)
    h.tick(st(pos=12.0, playing=False), 3 + IDLE)
    rows = h.read()
    assert [(r["heard"], r["finished"]) for r in rows] == [(12.0, False), (30.0, False)]


def test_a_pause_shorter_than_idle_is_the_same_listen(tmp_path):
    h = History(str(tmp_path))
    h.tick(st(pos=10.0), 0)
    h.tick(st(pos=10.0, playing=False), 1)
    h.tick(st(pos=11.0), 50)
    assert len(h.read()) == 1
