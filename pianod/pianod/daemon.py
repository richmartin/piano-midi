"""pianod: plays the MIDI library and runs Performance mode on the piano it is cabled to.

The Pi is the source of truth. Clients talk newline-delimited JSON, one request per line, one
reply per line:
  - the panel (PyBadge), over its USB data port (found by itself, reconnected by itself);
  - the `piano` command, over a Unix socket;
  - later, the web remote, relayed by the server over the same protocol.
pianod also pushes {"type": "state", ...} to the panel whenever anything changes, and every
second regardless, so the panel never shows something stale for long.

Same bones as hotpi's heatd, deliberately.
"""

from __future__ import annotations

import base64
import glob
import json
import logging
import os
import queue
import random
import socket
import threading
import time

from . import (calibrate, calibration, companion, history, library, pipeline, scores, sources, live, looper, midiio, network, observe, player,
               smf, store, web)

LOG = logging.getLogger("pianod")

DATA = os.environ.get("PIANO_DATA", "/var/lib/piano")
SOCK = os.environ.get("PIANO_SOCK", "/run/piano/pianod.sock")
# Warnings and errors also go here, as syslog over UDP ("host:port"), so they can be read off the
# Pi: its journal is in RAM and goes with every reboot. Unset: the journal only.
SYSLOG = os.environ.get("PIANO_SYSLOG", "")
SERIAL_GLOB = os.environ.get("PIANO_SERIAL", "/dev/serial/by-id/*Adafruit*-if02")
SERVER_URL = os.environ.get("PIANO_SERVER", "http://piano.fam-mart.in")     # LAN only
PAGE_BYTES = 3000              # the badge drops any line over 4 KB: lists go in pages under it


# The panel asks for lists with "compact": rows of values in these orders instead of dicts,
# about a third of the bytes, and far fewer objects in its 192 KB of RAM.
COMPOSER_ROW = ("slug", "short", "first", "born", "died", "era", "works")
PIANIST_ROW = ("slug", "name", "short", "born", "died", "works")
WORK_ROW = ("id", "title", "movement", "performer", "duration", "composer")


def compact(req: dict, items: list[dict], fields: tuple) -> list:
    if not req.get("compact"):
        return items
    return [[it.get(f) for f in fields] for it in items]


def brief(w: dict, composer: bool = False) -> dict:
    """A work as a list shows it: its pianist (short) if it's a roll, else its length."""
    out = {"id": w["id"], "title": w["title"], "movement": w.get("movement"),
           "performer": w.get("performer_short"), "duration": w.get("duration")}
    if composer:
        out["composer"] = w.get("composer_short") or w.get("composer")
    return out


def page(req: dict, key: str, items: list) -> dict:
    """All the items, or with "offset" in the request, the page from there that fits in
    PAGE_BYTES, and where the next one starts (None: that was the last)."""
    if "offset" not in req:
        return {key: items}
    start = int(req["offset"])
    out, size = [], 0
    for it in items[start:]:
        n = len(json.dumps(it)) + 2
        if out and size + n > PAGE_BYTES:
            break
        out.append(it)
        size += n
    end = start + len(out)
    return {key: out, "next": end if end < len(items) else None}
MIDI_OUT = os.environ.get("PIANO_MIDI_OUT", "")      # substring of the port name; "" = first
MIDI_IN = os.environ.get("PIANO_MIDI_IN", "")
ECHO_MS = float(os.environ.get("PIANO_ECHO_MS", "250"))             # 0 if the piano doesn't echo
POLL = 0.005                  # looper tick
OUTBOX = 200                  # messages queued for a client before it's deemed not reading
MIC_CHECK_EVERY = 30.0        # seconds between looks for a working mic
SYNC_FIRST, SYNC_EVERY = 120.0, 3600.0    # looks for a republished library, when idle
PUSH_EVERY = 1.0              # unprompted state pushes (position moves, so push often)
KEYS_EVERY = 0.05             # the panel's live keys: at most twenty batches a second
MODES = ("library", "performance")


def notify(msg: str) -> None:
    """systemd's READY/WATCHDOG, when running under it."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        try:
            s.sendto(msg.encode(), addr)
        except OSError:
            pass


class Pianod:
    def __init__(self, out=None, midi_in=None):
        self.lock = threading.RLock()
        os.makedirs(DATA, exist_ok=True)
        # sheet music turned into works (scores.py); ready ones join the library under their composer
        self.scores = scores.Scores(DATA, pipeline.convert, on_ready=self._scores_changed,
                                    accepts=pipeline.ACCEPTS)
        self.lib = library.Library(DATA, scores=self.scores.ready)
        if out is None:
            out, midi_in = midiio.open_ports(MIDI_OUT, MIDI_IN)
        self.out = out
        self.midi_in = midi_in
        self.gate = midiio.EchoGate(window=ECHO_MS / 1000)
        self.cal = calibration.load(f"{DATA}/calibration.json")
        self.player = player.Player(self.out, on_event=self.gate.sent, calibration=self.cal)
        self.player.on_ended = self._track_ended
        self.looper = looper.Looper(self.out, on_event=self.gate.sent, calibration=self.cal)
        self.live = live.Live(self.out, self.cal, on_event=self.gate.sent)   # Saxy's Band
        self.observer = observe.Observer(DATA, self.cal)
        self.out.observer = self.observer
        self.mode = "library"
        self.queue: list[dict] = []          # [{id, title, composer}]
        self.index = -1
        self.listeners: list = []
        self.favourites: list[str] = store.read_json(f"{DATA}/favourites.json", [])
        self.history = history.History(DATA)
        self.keys_pending: dict[int, bool] = {}   # the piano's keys, for the panel's strip
        self.keys_later: set[int] = set()
        self.keys_sent = 0.0
        self.last_input = 0.0                # monotonic: someone played the keys
        self.calib: calibrate.Calibrator | None = None
        self.calib_summary: list[str] = []   # the last calibration's findings
        self.mic: dict = {"ok": False, "why": "not checked yet"}   # calibrate.probe_mic()
        st = store.read_json(f"{DATA}/state.json", {})
        if 1 <= st.get("volume", 0) <= 11:
            self.player.volume = self.live.volume = st["volume"]

    def _save_state(self):
        store.write_json(f"{DATA}/state.json", {"volume": self.player.volume})

    # --- state ---------------------------------------------------------------------------
    def state(self) -> dict:
        with self.lock:
            cur = self.queue[self.index] if 0 <= self.index < len(self.queue) else None
            return {
                "type": "state",
                "mode": self.mode,
                "playing": self.player.playing,
                "id": cur["id"] if cur else None,
                "title": cur["title"] if cur else None,
                "movement": cur.get("movement") if cur else None,
                "composer": (cur.get("composer_short") or cur["composer"]) if cur else None,
                "performer": cur.get("performer_name") if cur else None,
                "roll": cur.get("roll") if cur else None,
                "fav": bool(cur) and cur["id"] in self.favourites,
                "pos": round(self.player.position(), 1),
                "len": round(self.player.duration, 1),
                "index": self.index,
                "count": len(self.queue),
                "next_title": self.queue[self.index + 1]["title"] if 0 <= self.index + 1 < len(self.queue) else None,
                "volume": self.player.volume,
                "midi_out": self.out.connected,
                "midi_in": self.midi_in is not None,
                "works": len(self.lib.all_works()),
                "perf": self.looper.status(),
                "calib": self.calib.status() if self.calib else None,
                "mic": self.mic,
                "live": self.live.status(),
            }

    def _push(self):
        s = self.state()
        for fn in list(self.listeners):
            try:
                fn(s)
            except Exception:    # a client that's gone away; its own thread will tidy up
                pass

    # --- the queue -------------------------------------------------------------------------
    def _load_current(self) -> None:
        cur = self.queue[self.index]
        work = self.lib.work(cur["id"])
        events, duration = smf.load(self.lib.path(work))
        # Our own takes are already in the piano's sensor units: no fitting.
        take = work["url"].startswith("/recordings/")
        self.player.load(events, duration, cur["title"], fit=not take)
        self.observer.source = "take" if take else "library"

    def _play_queue(self, works: list[dict], shuffle: bool = False) -> None:
        if not works:
            raise ValueError("nothing to play")
        if shuffle:
            works = random.sample(works, len(works))
        self.queue = [{"id": w["id"], "title": w["title"], "composer": w.get("composer", "")} for w in works]
        self.index = 0
        self._load_current()
        self.player.play()

    def _track_ended(self):
        """From the player's worker thread: the piece finished; on to the next."""
        with self.lock:
            if self.mode != "library" or self.index + 1 >= len(self.queue):
                self._push()
                return
            self.index += 1
            try:
                self._load_current()
                self.player.play()
            except (OSError, ValueError) as e:
                LOG.warning("skipping %s: %s", self.queue[self.index]["id"], e)
                self._track_ended()
                return
        self._push()

    # --- requests ------------------------------------------------------------------------
    def handle(self, req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd == "log":
            # The panel's own errors (its tracebacks), to land in the same log as ours.
            LOG.warning("panel: %s", str(req.get("text", ""))[:2000])
            return {"ok": True}
        try:
            reply = self._handle_fresh(cmd, req)
        except (ValueError, OSError, looper.LooperError) as e:
            LOG.warning("%s failed: %s (%s)", cmd, e, _brief_req(req))
            return {"ok": False, "error": str(e)}
        except KeyError as e:
            LOG.warning("%s failed: missing %s (%s)", cmd, e, _brief_req(req))
            return {"ok": False, "error": f"{cmd}: missing {e}"}
        except Exception as e:                          # a bug: say so, and keep serving
            LOG.exception("%s crashed (%s)", cmd, _brief_req(req))
            return {"ok": False, "error": f"{cmd}: {type(e).__name__}: {e}"}
        if cmd not in ("status", "composers", "works", "queue", "recordings", "calibrate_summary",
                       "net", "wifi_scan", "wifi_log", "companion", "pianists", "pianist_works", "collection",
                       "favourites", "shelves", "observe", "history", "score_search", "scores"):
            self._push()
        return {"ok": True, **(reply or {})}

    def _handle_fresh(self, cmd, req):
        """_handle, but a work this mirror hasn't heard of yet is fetched first: the web player
        is served by the librarian, so it shows a republished library before the hourly sync."""
        try:
            return self._handle(cmd, req)
        except ValueError as e:
            if not str(e).startswith("no such work") or cmd not in ("play", "fav"):
                raise
        ids = set(req.get("ids") or [req.get("id")])
        LOG.info("%s: catching up with the library for %s", cmd, ", ".join(map(str, ids)))
        self.lib.sync(SERVER_URL, only=ids)
        return self._handle(cmd, req)

    def _companion(self, req):
        """A turn of conversation (slow: two calls to Zeus), outside the lock."""
        if req.get("probe"):
            return {"ready": companion.ready()}
        if not companion.ready():
            raise ValueError("the companion isn't set up (no ZEUS_URL/ZEUS_TOKEN)")
        try:
            r = companion.turn(self.lib, list(req.get("messages") or []), list(self.favourites),
                               self.history.read(40), self.state())
        except companion.CompanionError as e:
            raise ValueError(f"the companion is unavailable: {e}") from e
        if r["action"] == "play":
            res = self.handle({"cmd": "play", "ids": [w["id"] for w in r["works"]]})
            if not res.get("ok"):
                r["action"] = "suggest"
                r["error"] = res.get("error")
        return r

    def _scores_changed(self):
        with self.lock:
            self.lib.load()
        self._push()

    def _score(self, cmd, req):
        """The Scores browser: search the sources, import, and the list of what's been imported."""
        if cmd == "score_search":
            return {"results": sources.search(str(req.get("q") or ""))}
        if cmd == "scores":
            return {"scores": self.scores.list(), "pipeline": pipeline.ready()}
        if cmd == "score_import":                   # a search result pianod can fetch itself
            return {"score": self.scores.add_item(dict(req["item"]))}
        if cmd == "score_upload":                   # a file Rich brought (an IMSLP PDF, say)
            data = base64.b64decode(str(req["data_b64"]), validate=True)
            return {"score": self.scores.add_upload(str(req["name"]), data, {
                "title": req.get("title"), "composer": req.get("composer"), "page": req.get("page")})}
        if cmd == "score_add":                      # a performance made elsewhere (Saxy's handoff)
            midi = base64.b64decode(str(req["midi_b64"]), validate=True)
            xml = base64.b64decode(str(req["musicxml_b64"]), validate=True) if req.get("musicxml_b64") else None
            rec = self.scores.add_ready({k: req.get(k) for k in ("title", "composer", "detail", "source",
                                                                  "performance")}, midi, xml)
            return {"id": rec["id"], "score": rec}
        if cmd == "score_retry":
            return {"score": self.scores.retry(str(req["id"]))}
        if cmd == "score_reimport":                 # a fresh reading of the file it was made from
            return {"score": self.scores.reimport(str(req["id"]))}
        if cmd == "score_refresh":                  # Saxy re-performed it: fetch the new MIDI
            return {"score": self.scores.refresh(str(req["id"]))}
        if cmd == "score_remove":
            self.scores.remove(str(req["id"]))
            self._scores_changed()
            return {}
        raise ValueError(f"unknown command {cmd!r}")

    def _auto_sync(self):
        time.sleep(SYNC_FIRST)
        while True:
            with self.lock:
                busy = self.player.playing or self.live.status() or self._calibrating()
            if not busy:
                try:
                    r = self.lib.sync(SERVER_URL)
                    if r["changed"] or r["fetched"]:
                        LOG.info("library sync: %(works)d works, %(fetched)d fetched", r)
                        self.lib.measure()
                        self._push()
                except (OSError, ValueError) as e:
                    LOG.warning("library sync: %s", e)
            time.sleep(SYNC_EVERY)

    def _calibrating(self) -> bool:
        return self.calib is not None and not self.calib.finished

    def _handle(self, cmd, req):
        with self.lock:
            if self._calibrating() and cmd not in (
                    "status", "composers", "works", "recordings", "queue", "calibrate_cancel",
                    "calibrate_summary", "panic", "net", "wifi_scan", "wifi_join", "wifi_log",
                    "pianists", "pianist_works", "collection", "favourites", "shelves", "fav"):
                raise ValueError("calibrating (cancel it first)")
            if cmd == "status":
                return {"state": self.state()}
            if cmd == "composers":
                return page(req, "composers", compact(req, self.lib.composer_list(), COMPOSER_ROW))
            if cmd == "works":
                return page(req, "works", compact(req, [brief(w) for w in self.lib.works_of(req["slug"])], WORK_ROW))
            if cmd == "recordings":
                return page(req, "works", compact(req, [brief(w) for w in self.lib.recordings()], WORK_ROW))
            if cmd == "pianists":
                return page(req, "pianists", compact(req, self.lib.pianist_list(), PIANIST_ROW))
            if cmd == "pianist_works":
                return page(req, "works", compact(req, [brief(w, composer=True)
                                                        for w in self.lib.pianist_works(req["slug"])], WORK_ROW))
            if cmd == "collection":
                return page(req, "works", compact(req, [brief(w, composer=True)
                                                        for w in self.lib.collection(req.get("collection") or req["slug"])],
                                                    WORK_ROW))
            if cmd == "favourites":
                return page(req, "works", compact(req, [brief(w, composer=True)
                                                        for w in self._favourite_works()], WORK_ROW))
            if cmd == "shelves":
                return {"composers": sum(1 for c in self.lib.composers if c.get("slug") != "unknown"),
                        "pianists": len(self.lib.pianists), "favourites": len(self._favourite_works()),
                        "takes": len(self.lib.recordings()), "collections": self.lib.collections()}
            if cmd == "fav":
                self.lib.work(req["id"])                     # it exists
                favs = self.favourites
                fav = req["id"] not in favs
                if fav:
                    favs.append(req["id"])
                else:
                    favs.remove(req["id"])
                store.write_json(f"{DATA}/favourites.json", favs)
                return {"fav": fav}
            if cmd == "queue":
                return {"queue": [w["title"] for w in self.queue], "index": self.index}
            if cmd == "history":
                return {"history": self.history.read(int(req.get("limit") or 200))}

            if cmd == "play":
                # One of: ids (a list), slug (a composer), all (everything); optional shuffle.
                self._require_library_mode()
                if "ids" in req:
                    works = [self.lib.work(i) for i in req["ids"]]
                elif "slug" in req:
                    works = self.lib.works_of(req["slug"])
                elif "pianist" in req:
                    works = self.lib.pianist_works(req["pianist"])
                elif "collection" in req:
                    works = self.lib.collection(req["collection"])
                elif req.get("favourites"):
                    works = self._favourite_works()
                elif req.get("all"):
                    works = sorted(self.lib.all_works(), key=lambda w: (w["composer"], w["title"]))
                else:
                    raise ValueError("play needs ids, slug, pianist, collection, favourites or all")
                if not works:
                    raise ValueError("nothing to play there")
                self._play_queue(works, bool(req.get("shuffle")))
                return {}
            if cmd == "toggle":
                self._require_library_mode()
                if self.player.playing:
                    self.player.pause()
                elif self.player.events:
                    self.player.play()
                elif self.queue:
                    self._load_current()
                    self.player.play()
                else:
                    raise ValueError("nothing queued: choose something to play")
                return {}
            if cmd == "pause":
                self.player.pause()
                return {}
            if cmd == "resume":
                self._require_library_mode()
                self.player.play()
                return {}
            if cmd == "stop":
                self.player.stop()
                self.live.cancel()
                return {}
            if cmd in ("next", "prev"):
                self._require_library_mode()
                if not self.queue:
                    raise ValueError("nothing queued")
                step = 1 if cmd == "next" else -1
                self.index = max(0, min(len(self.queue) - 1, self.index + step))
                was_playing = self.player.playing
                self._load_current()
                if was_playing:
                    self.player.play()
                return {}
            if cmd == "seek":
                self.player.seek(float(req["t"]))
                return {}
            if cmd == "volume":
                self.player.set_volume(int(req["v"]))
                self.live.volume = self.player.volume
                self._save_state()
                return {}
            if cmd == "panic":
                if self._calibrating():
                    self.calib.rig.cancel.set()
                self.player.pause()
                self.live.cancel()
                self.out.panic()
                return {}

            if cmd == "live":
                # Timed notes from outside (Saxy's Band): see live.py.
                dry = bool(req.get("dryRun"))
                if not dry:
                    self._require_library_mode(live_ok=True)
                    if self.player.playing:
                        raise ValueError(f"the piano is playing {self.player.title!r}")
                if not dry:
                    self.observer.source = "live"
                return self.live.add(req.get("source"), req["events"], dry=dry)
            if cmd == "observe":
                return {"observe": self.observer.status()}
            if cmd == "live_cancel":
                return self.live.cancel(req.get("source"))

            if cmd == "calibrate":
                # Optional delay in minutes, to get out of the house first.
                delay = max(0.0, float(req.get("delay", 0))) * 60
                if not self.mic.get("ok"):
                    raise ValueError(f"no working mic ({self.mic.get('why')}): calibration "
                                     "needs one to hear the piano")
                self.player.stop()
                self.looper.clear()
                self.live.cancel()
                rig = calibrate.MidiRig(self.out, calibrate.Mic.find())
                self.calib = calibrate.Calibrator(rig, delay=delay)
                self.calib_summary = []
                threading.Thread(target=self._run_calibration, daemon=True, name="calibrate").start()
                return {}
            if cmd == "calibrate_summary":
                # Its own request, not in the pushed state: it can run to dozens of lines, and
                # the badge drops any line over 4 KB.
                return {"lines": self.calib_summary[:int(req.get("limit", 1000))]}
            if cmd == "calibrate_cancel":
                if not self._calibrating():
                    raise ValueError("not calibrating")
                self.calib.rig.cancel.set()
                return {}

            if cmd == "mode":
                mode = req.get("mode")
                if mode not in MODES:
                    raise ValueError(f"mode must be one of {', '.join(MODES)}")
                if mode != self.mode:
                    self.player.stop()
                    self.live.cancel()
                    self.looper.clear()
                    self.mode = mode
                    self.observer.source = "loop" if mode == "performance" else "library"
                return {}

            if cmd in ("perf_record", "perf_close_loop", "perf_take", "perf_play",
                       "perf_overdub", "perf_undo", "perf_redo", "perf_clear"):
                if self.mode != "performance":
                    raise ValueError("not in performance mode")
                {"perf_record": self.looper.record,
                 "perf_close_loop": self.looper.close_loop,
                 "perf_take": self.looper.finish_take,
                 "perf_play": self.looper.toggle_play,
                 "perf_overdub": self.looper.toggle_overdub,
                 "perf_undo": self.looper.undo,
                 "perf_redo": self.looper.redo,
                 "perf_clear": self.looper.clear}[cmd]()
                return {}
            if cmd == "perf_save":
                if not self.looper.layers:
                    raise ValueError("nothing recorded")
                title = str(req.get("title") or "").strip() or time.strftime("Take %Y-%m-%d %H.%M")
                path = self.lib.recording_path(title)
                smf.save(path, self.looper.merged_events(), title)
                return {"saved": os.path.basename(path)}

        if cmd == "companion":
            return self._companion(req)
        if cmd.startswith("score"):
            return self._score(cmd, req)        # network and files: outside the lock
        if cmd == "sync":
            # The downloads run outside the lock; the mirror swap inside library.sync is atomic
            # per file, and reads of self.lib go through self.lock only for the lists, which
            # library.load() replaces whole.
            with self.lock:
                if self.player.playing:
                    raise ValueError("busy playing; sync later")
            result = self.lib.sync(SERVER_URL)
            threading.Thread(target=self.lib.measure, daemon=True, name="durations").start()
            return result
        # The network (the panel is the Pi's only screen): slow, so outside the lock.
        if cmd == "net":
            return {"net": network.status()}
        if cmd == "wifi_scan":
            return {"networks": network.scan()}
        if cmd == "wifi_join":
            ok, msg = network.join(req["ssid"], req.get("psk") or None)
            if not ok:
                raise ValueError(msg)
            return {"message": msg}
        if cmd == "wifi_log":
            return page(req, "lines", network.log())
        raise ValueError(f"unknown command {cmd!r}")

    def _run_calibration(self):
        c = self.calib
        self.observer.enabled = False                 # it measures the same things itself
        try:
            c.run()
        finally:
            self.observer.enabled = True
        self.out.panic()
        c.rig.close()
        with self.lock:
            if c.phase == "done":
                try:
                    self.calib_summary = c.save(DATA, self.cal)
                except OSError as e:
                    c.phase, c.message = "failed", f"couldn't save: {e}"
                else:
                    self.cal = calibration.load(f"{DATA}/calibration.json")
                    self.player.cal = self.looper.cal = self.live.cal = self.observer.cal = self.cal
            LOG.info("calibration %s %s", c.phase, c.message)
            for line in self.calib_summary:
                LOG.info("calibration: %s", line)
        self._push()

    def _watch_mic(self):
        """Keep self.mic current, so the panel can offer Calibrate only when it can work."""
        while True:
            if not self._calibrating():            # mid-run, the calibration has the mic
                mic = calibrate.probe_mic()
                same = {**mic, "dbfs": None} == {**self.mic, "dbfs": None}   # level always moves
                self.mic = mic
                if not same:
                    self.mic = mic
                    self._push()
            time.sleep(MIC_CHECK_EVERY)

    def _require_library_mode(self, live_ok: bool = False):
        if self.mode != "library":
            raise ValueError("in performance mode (switch back to the library first)")
        if not live_ok and self.live.active():
            raise ValueError(f"the piano is playing {self.live.source}'s notes (stop first)")

    # --- MIDI in --------------------------------------------------------------------------
    def _midi_in_loop(self):
        """The piano's keys: feed Performance mode, and note that someone is playing."""
        while True:
            port = self.midi_in
            if port is None:
                time.sleep(3)
                continue
            try:
                msg = port.receive()
            except (OSError, RuntimeError) as e:
                LOG.warning("MIDI in failed (%s)", e)
                self.midi_in = None
                continue
            kind = None
            if msg.type == "note_on" and msg.velocity > 0:
                kind, a, b = "on", msg.note, msg.velocity
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                kind, a, b = "off", msg.note, 0
            elif msg.type == "control_change" and msg.control in smf.PEDALS:
                kind, a, b = "cc", msg.control, msg.value
            if kind is None:
                continue
            if kind in ("on", "off"):
                self._key_moved(a, kind == "on")
            calib = self.calib
            if calib is not None and not calib.finished:
                calib.rig.feed(kind, a, b)           # raw: the calibration wants the echoes
                continue
            if kind == "on":
                self.observer.echo(a, b)
            if kind in ("on", "off") and self.gate.is_echo(kind, a):
                continue
            self.last_input = time.monotonic()
            if self.mode == "performance":
                self.looper.note_in(kind, a, b)

    def _favourite_works(self) -> list[dict]:
        out = []
        for i in self.favourites:
            try:
                out.append(self.lib.work(i))
            except ValueError:                        # gone from the library since
                pass
        return out

    # --- the keys, live, for the panel -------------------------------------------------------
    def _key_moved(self, key: int, down: bool) -> None:
        """Every key the piano reports, playback and people alike. A key pressed and released
        within one batch shows pressed in this batch and released in the next, so short notes
        still flash."""
        with self.lock:
            if not down and self.keys_pending.get(key) is True:
                self.keys_later.add(key)
            else:
                self.keys_pending[key] = down
                self.keys_later.discard(key)

    def _flush_keys(self, now: float) -> None:
        if now - self.keys_sent < KEYS_EVERY:
            return
        with self.lock:
            if not self.keys_pending:
                return
            msg = {"type": "keys", "on": [k for k, v in self.keys_pending.items() if v],
                   "off": [k for k, v in self.keys_pending.items() if not v]}
            self.keys_pending = {k: False for k in self.keys_later}
            self.keys_later = set()
        self.keys_sent = now
        for fn in list(self.listeners):
            try:
                fn(msg)
            except Exception:
                pass

    # --- running ---------------------------------------------------------------------------
    def run(self):
        threading.Thread(target=serve_socket, args=(self,), daemon=True, name="socket").start()
        threading.Thread(target=serve_panel, args=(self,), daemon=True, name="panel").start()
        threading.Thread(target=self._midi_in_loop, daemon=True, name="midi-in").start()
        threading.Thread(target=self._watch_mic, daemon=True, name="mic-check").start()
        threading.Thread(target=self.lib.measure, daemon=True, name="durations").start()
        threading.Thread(target=self._auto_sync, daemon=True, name="library-sync").start()
        threading.Thread(target=self.scores.run, daemon=True, name="scores").start()
        web.serve(self, DATA)
        notify("READY=1")
        LOG.info("pianod up: %d works, MIDI out %s", len(self.lib.all_works()),
                 "connected" if self.out.connected else "NOT connected")
        last_push = time.monotonic()
        while True:
            self.looper.poll()
            self.observer.poll()
            time.sleep(POLL)
            t = time.monotonic()
            self._flush_keys(t)
            if t - last_push >= PUSH_EVERY:
                self._push()
                self.history.tick(self.state(), t)
                notify("WATCHDOG=1")
                last_push = t


def _serve_lines(d: Pianod, read_line, write_line, push: bool, name: str):
    """One client: read requests, write replies (and pushes, for the panel), until it goes away."""
    # Writes go through an outbox and a thread of their own: a client that stops reading (the
    # badge wedged, 2026-11-02 09:18) used to block the write, and with it whichever thread was
    # pushing -- the MIDI in, then the playback: Rhapsody in Blue stopped mid-bar.
    outbox: queue.Queue = queue.Queue(maxsize=OUTBOX)
    dropping = []

    def writer():
        while True:
            s = outbox.get()
            if s is None:
                return
            try:
                write_line(s)
            except Exception:           # gone; the reader will notice
                return

    threading.Thread(target=writer, daemon=True, name=f"{name}-out").start()

    def send(obj):
        try:
            outbox.put_nowait(json.dumps(obj, separators=(",", ":")) + "\n")
            dropping.clear()
        except queue.Full:
            if not dropping:
                LOG.warning("%s isn't reading; dropping what it's sent until it does", name)
                dropping.append(1)

    if push:
        d.listeners.append(send)
        send(d.state())
    try:
        while True:
            line = read_line()
            if line is None:
                return
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                send({"ok": False, "error": "not JSON"})
                continue
            reply = d.handle(req)
            if "rid" in req:                    # the panel's request number, for its reply
                reply["rid"] = req["rid"]
            elif "id" in req and req.get("cmd") != "fav":   # older panels; "fav" uses id for the work
                reply["id"] = req["id"]
            send(reply)
    finally:
        if push and send in d.listeners:
            d.listeners.remove(send)
        try:
            outbox.put_nowait(None)
        except queue.Full:
            pass
        (LOG.info if push else LOG.debug)("%s disconnected", name)


def serve_socket(d: Pianod):
    os.makedirs(os.path.dirname(SOCK), exist_ok=True)
    try:
        os.unlink(SOCK)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(SOCK)
    except OSError as e:
        # A daemon nobody can talk to is worse than a dead one: die loudly, systemd restarts us.
        LOG.error("can't bind %s: %s", SOCK, e)
        os._exit(1)
    os.chmod(SOCK, 0o666)      # the `piano` CLI runs as rich; pianod as root under systemd
    srv.listen(4)
    while True:
        conn, _ = srv.accept()
        f = conn.makefile("rw", buffering=1, encoding="utf-8")

        def rl(f=f):
            line = f.readline()
            return line or None

        threading.Thread(target=_serve_lines, args=(d, rl, f.write, False, "cli"), daemon=True).start()


def serve_panel(d: Pianod):
    """Find the PyBadge's data port, talk to it, and look for it again when it goes."""
    import serial
    while True:
        ports = sorted(glob.glob(SERIAL_GLOB))
        if not ports:
            time.sleep(0.5)                 # at boot the badge appears any moment; don't keep it waiting
            continue
        try:
            port = serial.Serial(ports[0], 115200, timeout=None)
        except (OSError, serial.SerialException) as e:
            LOG.warning("panel port %s: %s", ports[0], e)
            time.sleep(3)
            continue
        LOG.info("panel connected on %s", ports[0])

        def rl():
            try:
                b = port.readline()
            except (OSError, serial.SerialException):
                return None
            return b.decode("utf-8", "replace") if b else None

        def wl(s):
            port.write(s.encode())

        try:
            _serve_lines(d, rl, wl, True, "panel")
        finally:
            try:
                port.close()
            except Exception:
                pass
        time.sleep(1)


def _brief_req(req: dict) -> str:
    return json.dumps({k: v for k, v in req.items() if k not in ("psk",)})[:300]


def _remote_log(target: str) -> None:
    """Copy warnings and errors to a syslog receiver on the LAN, "host:port" (UDP) — and the
    live audit ("pianod.audit", one line per accepted non-dry batch) at INFO: the record that
    something moved hammers has to outlive the Pi's in-RAM journal."""
    import logging.handlers

    def handler():
        h = logging.handlers.SysLogHandler(address=(host, int(port)),
                                           facility=logging.handlers.SysLogHandler.LOG_LOCAL0)
        h.ident = "pianod: "                # the syslog tag the receiver files by
        h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        return h

    host, _, port = target.rpartition(":")
    try:
        root = handler()
        audit = handler()
    except (OSError, ValueError) as e:
        LOG.warning("no remote log at %s: %s", target, e)
        return
    root.setLevel(logging.WARNING)
    logging.getLogger().addHandler(root)
    audit.setLevel(logging.INFO)            # the root handler's WARNING filter would drop these
    logging.getLogger("pianod.audit").addHandler(audit)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if SYSLOG:
        _remote_log(SYSLOG)
    Pianod().run()
