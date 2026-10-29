"""piano: talk to pianod from a shell (over SSH, say).

  piano                         what the piano is doing
  piano composers               the library, by composer
  piano works SLUG              one composer's pieces
  piano play SLUG | all | shuffle | ID [ID...]
  piano toggle | pause | resume | stop | next | prev
  piano volume 1-11
  piano mode library|performance
  piano record | close | take | perf-play | overdub | undo | redo | perf-clear
  piano save [TITLE]            save the take/loop into recordings/
  piano recordings
  piano sync                    refresh the library mirror from the server
  piano panic                   silence, now
  piano calibrate [MINUTES]     measure every key (up to an hour), optionally starting later
  piano calibrate cancel
"""

from __future__ import annotations

import json
import socket
import sys

from .daemon import SOCK


def request(req: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(SOCK)
        f = s.makefile("rw", buffering=1, encoding="utf-8")
        f.write(json.dumps(req) + "\n")
        return json.loads(f.readline())


def show(st: dict) -> None:
    c = st.get("calib")
    if c:
        line = f"Calibration: {c['phase']}"
        if c["phase"] == "waiting":
            line += f", starts in {c['starts_in'] // 60}:{c['starts_in'] % 60:02d}"
        if c["phase"] in ("measuring", "paused"):
            line += f", {c['key']} ({c['done']}/{c['of']} keys)"
            if c.get("eta") is not None:
                line += f", ~{c['eta'] // 60} min left"
        if c["message"]:
            line += f": {c['message']}"
        if c["flags"]:
            line += f", {c['flags']} flagged"
        print(line)
        if c["phase"] == "done":
            for s in request({"cmd": "calibrate_summary"}).get("lines", []):
                print(f"  {s}")
    if st["mode"] == "performance":
        p = st["perf"]
        line = f"Performance: {p['state']}"
        if p["layers"]:
            line += f", {p['layers']} layer{'s' if p['layers'] != 1 else ''}"
        if p["length"]:
            line += f", {'loop' if p['looping'] else 'take'} {p['length']:.1f}s at {p['pos']:.1f}s"
        if p["overdub"]:
            line += ", OVERDUB"
        print(line)
    elif st["title"]:
        print(f"{'Playing' if st['playing'] else 'Paused '} {st['title']} ({st['composer']})"
              f"  {st['pos']:.0f}/{st['len']:.0f}s  [{st['index'] + 1}/{st['count']}]")
        if st.get("next_title"):
            print(f"Next    {st['next_title']}")
    else:
        print("Idle")
    print(f"Volume  {st['volume']}/11   MIDI out {'ok' if st['midi_out'] else 'MISSING'}"
          f"   in {'ok' if st['midi_in'] else 'MISSING'}   {st['works']} works")
    mic = st.get("mic")
    if mic:
        print(f"Mic     {mic['name'] if mic.get('ok') else mic.get('why')}")


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a.pop(0) if a else "status"
    if cmd == "play" and a:
        if a[0] == "all":
            req = {"cmd": "play", "all": True}
        elif a[0] == "shuffle":
            req = {"cmd": "play", "all": True, "shuffle": True}
        elif a[0].startswith(("file-id-", "recording:")):
            req = {"cmd": "play", "ids": a}
        else:
            req = {"cmd": "play", "slug": a[0]}
    else:
        reqs = {
            "status": lambda: {"cmd": "status"},
            "composers": lambda: {"cmd": "composers"},
            "works": lambda: {"cmd": "works", "slug": a[0] if a else ""},
            "toggle": lambda: {"cmd": "toggle"},
            "pause": lambda: {"cmd": "pause"},
            "resume": lambda: {"cmd": "resume"},
            "stop": lambda: {"cmd": "stop"},
            "next": lambda: {"cmd": "next"},
            "prev": lambda: {"cmd": "prev"},
            "volume": lambda: {"cmd": "volume", "v": int(a[0])},
            "mode": lambda: {"cmd": "mode", "mode": a[0] if a else ""},
            "record": lambda: {"cmd": "perf_record"},
            "close": lambda: {"cmd": "perf_close_loop"},
            "take": lambda: {"cmd": "perf_take"},
            "perf-play": lambda: {"cmd": "perf_play"},
            "overdub": lambda: {"cmd": "perf_overdub"},
            "undo": lambda: {"cmd": "perf_undo"},
            "redo": lambda: {"cmd": "perf_redo"},
            "perf-clear": lambda: {"cmd": "perf_clear"},
            "save": lambda: {"cmd": "perf_save", **({"title": " ".join(a)} if a else {})},
            "recordings": lambda: {"cmd": "recordings"},
            "sync": lambda: {"cmd": "sync"},
            "panic": lambda: {"cmd": "panic"},
            "calibrate": lambda: ({"cmd": "calibrate_cancel"} if a and a[0] == "cancel"
                                  else {"cmd": "calibrate", "delay": float(a[0]) if a else 0}),
        }
        if cmd not in reqs:
            print(__doc__.strip())
            return 2
        try:
            req = reqs[cmd]()
        except (IndexError, ValueError):
            print(__doc__.strip())
            return 2
    try:
        r = request(req)
    except OSError as e:
        print(f"can't reach pianod at {SOCK}: {e}", file=sys.stderr)
        return 1
    if not r.get("ok"):
        print(f"error: {r.get('error')}", file=sys.stderr)
        return 1
    if cmd == "composers":
        for c in r["composers"]:
            print(f"{c['slug']:36} {c['works']:3}  {c['name']}")
    elif cmd in ("works", "recordings"):
        for w in r["works"]:
            print(f"{w['id']:24} {w['title']}")
    elif cmd == "sync":
        print(f"{r['works']} works in the library, {r['fetched']} fetched")
    elif cmd == "save":
        print(f"saved {r['saved']}")
    else:
        show(request({"cmd": "status"})["state"])
    return 0
