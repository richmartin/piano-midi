"""Scores turned into playable works: what the Scores browser imports, and the pipeline's results.

A score comes from a source (sources.py: fetched by pianod itself) or from Rich (an upload: a PDF
he downloaded from IMSLP, say). It is queued, sent through the sheet-music -> performance pipeline
(convert(), see PIPELINE below), and the MIDI that comes back becomes a work like any other: the
library merges ready scores in under their composer (Library.load), so the web page, the badge and
the companion all see them.

Layout under root/scores/:  index.json  (id -> the score's record),  <id>/input.<ext>,  <id>.mid

A record: {"id", "title", "composer", "source", "ref", "page", "licence", "input": "pdf"|"mid"|...,
           "state": "queued"|"converting"|"ready"|"failed", "error", "added", "done", "duration"}

One score converts at a time, in a thread of its own, so a long PDF never holds up playing.
States survive a restart: anything left "converting" is queued again.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
import urllib.request

from . import catalogue, smf, sources, store

LOG = logging.getLogger("pianod")
MAX_INPUT = 40 * 1024 * 1024            # a 100-page scan; anything bigger isn't a piano piece
INPUTS = ("mid", "xml", "mxl", "musicxml", "pdf", "ly")
UA = "pianod-score-browser/1 (a household piano; private use)"


class PipelineError(Exception):
    pass


def _download(url: str) -> bytes:
    req = urllib.request.Request(sources.safe_url(url), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise ValueError("that file is too big for a piano score")
    return data


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", catalogue.ascii_fold(text).lower()).strip("-")[:40] or "score"


TAIL_TICKS = 480                       # what's kept after the last note: a beat


def trim_tail(midi: bytes) -> bytes:
    """A score's MIDI with the silence after its last note cut to a beat. Saxy's first files closed
    their track about a minute after the music (2026-11-07), and pianod plays a file to its end."""
    import io

    import mido
    try:
        f = mido.MidiFile(file=io.BytesIO(midi))
    except Exception:
        return midi
    changed = False
    for track in f.tracks:
        if track and track[-1].type == "end_of_track" and track[-1].time > TAIL_TICKS:
            track[-1].time = TAIL_TICKS
            changed = True
    if not changed:
        return midi
    out = io.BytesIO()
    f.save(file=out)
    return out.getvalue()


class Scores:
    def __init__(self, root: str, convert, on_ready=None, fetch=_download, accepts=INPUTS):
        """convert(input_path, kind, record) -> MIDI bytes (raises PipelineError).
        on_ready(): called after a score turns ready (the daemon reloads the library).
        accepts: the input kinds the pipeline takes, best first."""
        self.dir = os.path.join(root, "scores")
        os.makedirs(self.dir, exist_ok=True)
        self.index_path = os.path.join(self.dir, "index.json")
        self.convert, self.on_ready, self.fetch, self.accepts = convert, on_ready, fetch, accepts
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.items: dict[str, dict] = store.read_json(self.index_path, {})
        for r in self.items.values():          # a restart part way through: try again
            if r["state"] == "converting":
                r["state"] = "queued"

    # --- the record ---------------------------------------------------------------------
    def _save(self) -> None:
        store.write_json(self.index_path, self.items)

    def _new_id(self, title: str) -> str:
        base = f"score-{_slug(title)}"
        sid, n = base, 2
        while sid in self.items:
            sid, n = f"{base}-{n}", n + 1
        return sid

    def list(self) -> list[dict]:
        with self.lock:
            return sorted((dict(r) for r in self.items.values()), key=lambda r: -r["added"])

    def midi_path(self, sid: str) -> str:
        if not re.fullmatch(r"score-[a-z0-9-]+", sid):
            raise ValueError(f"no such score {sid!r}")
        return os.path.join(self.dir, f"{sid}.mid")

    def ready(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.items.values() if r["state"] == "ready"]

    # --- adding -------------------------------------------------------------------------
    def _add(self, data: bytes, kind: str, meta: dict) -> dict:
        if kind not in self.accepts:
            raise ValueError(f"the pipeline can't read a .{kind} file (it takes "
                             f"{', '.join('.' + k for k in self.accepts)})")
        if not data:
            raise ValueError("the file is empty")
        with self.lock:
            sid = self._new_id(meta.get("title") or "score")
            os.makedirs(os.path.join(self.dir, sid), exist_ok=True)
            store.write_bytes(os.path.join(self.dir, sid, f"input.{kind}"), data)
            rec = {"id": sid, "title": (meta.get("title") or "Untitled").strip()[:120],
                   "composer": catalogue.canonical_name((meta.get("composer") or "Unknown Composer").strip()[:80]),
                   "detail": meta.get("detail") or "", "source": meta.get("source") or "upload",
                   "ref": meta.get("ref"), "page": meta.get("page"), "licence": meta.get("licence"),
                   "input": kind, "state": "queued", "error": None,
                   "added": time.time(), "done": None, "duration": None}
            self.items[sid] = rec
            self._save()
        self.wake.set()
        return dict(rec)

    def add_item(self, item: dict) -> dict:
        """A search result (sources.py) that pianod can fetch: the best format the pipeline takes."""
        formats = item.get("formats") or {}
        kind = next((k for k in self.accepts if k in formats), None)
        if kind is None:
            raise ValueError("pianod can't fetch this one: download it from the source's page and "
                             "import the file")
        url = formats[kind]
        if not url.startswith("https://"):
            raise ValueError("only https sources")
        return self._add(self.fetch(url), kind, item)

    def add_upload(self, name: str, data: bytes, meta: dict) -> dict:
        kind = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        title = meta.get("title") or re.sub(r"[_-]+", " ", name.rsplit(".", 1)[0])
        return self._add(data, kind, {**meta, "title": title, "source": "upload"})

    def add_ready(self, meta: dict, midi: bytes, musicxml: bytes | None = None) -> dict:
        """A performance made elsewhere (Saxy's "Keep on the piano"): ready at once. The same
        source and ref again (a re-perform) replaces the MIDI under the same id."""
        if not midi.startswith(b"MThd"):
            raise ValueError("that isn't a MIDI file")
        src = meta.get("source") or {}
        kind, ref = src.get("kind") or "saxy", src.get("ref")
        with self.lock:
            rec = next((r for r in self.items.values()
                        if ref and r.get("source") == kind and r.get("ref") == ref), None)
            if rec is None:
                sid = self._new_id(meta.get("title") or "score")
                rec = self.items[sid] = {"id": sid, "added": time.time()}
            sid = rec["id"]
            os.makedirs(os.path.join(self.dir, sid), exist_ok=True)
            if musicxml:
                store.write_bytes(os.path.join(self.dir, sid, "score.musicxml"), musicxml)
            store.write_bytes(self.midi_path(sid), trim_tail(midi))
            events, _ = smf.load(self.midi_path(sid))
            if not any(e.kind == "on" for e in events):
                raise ValueError("that MIDI has no notes")
            rec.update({"title": (meta.get("title") or "Untitled").strip()[:120],
                        "composer": catalogue.canonical_name((meta.get("composer") or "Unknown Composer").strip()[:80]),
                        "detail": meta.get("detail") or "", "source": kind, "ref": ref,
                        "page": src.get("page") or src.get("url"), "licence": src.get("licence"),
                        "input": "mid", "state": "ready", "error": None, "done": time.time(),
                        "duration": round(events[-1].time, 1),
                        "performance": meta.get("performance")})
            self._save()
        if self.on_ready:
            self.on_ready()
        return dict(rec)

    def retry(self, sid: str) -> dict:
        with self.lock:
            r = self.items.get(sid) or {}
            if r.get("state") != "failed":
                raise ValueError("only a failed score can be tried again")
            r.update(state="queued", error=None)
            self._save()
        self.wake.set()
        return dict(r)

    def refresh(self, sid: str) -> dict:
        """Fetch the pipeline's current performance again (Saxy re-performed it: a fixed reading, a
        new style). The score stays playable as it was until the new MIDI is in."""
        with self.lock:
            r = self.items.get(sid) or {}
            if r.get("state") not in ("ready", "failed") or not r.get("pipeline_id"):
                raise ValueError("only a score the pipeline has finished can be fetched again")
            r.update(refresh=True, error=None)
            if r["state"] == "failed":
                r["state"] = "queued"
            self._save()
        self.wake.set()
        return dict(r)

    def reimport(self, sid: str) -> dict:
        """A new score from the file this one was made from, with the same source details: a fresh
        reading and performance (the pipeline improved), without the source being reachable. The
        old score stays until it's removed."""
        with self.lock:
            r = dict(self.items.get(sid) or {})
        if not r or r.get("source") == "saxy":
            raise ValueError("only a score pianod sent to the pipeline can be imported again")
        with open(os.path.join(self.dir, sid, f"input.{r['input']}"), "rb") as f:
            data = f.read()
        return self._add(data, r["input"], {k: r.get(k) for k in
                                            ("title", "composer", "detail", "source", "ref", "page", "licence")})

    def remove(self, sid: str) -> None:
        with self.lock:
            r = self.items.pop(sid, None)
            if r is None:
                raise ValueError(f"no such score {sid!r}")
            self._save()
        try:
            os.remove(self.midi_path(sid))         # (midi_path checks the id: nothing outside scores/)
        except OSError:
            pass
        shutil.rmtree(os.path.join(self.dir, sid), ignore_errors=True)   # the file it was made from

    # --- converting ---------------------------------------------------------------------
    def _next(self):
        with self.lock:
            q = [r for r in self.items.values() if r["state"] == "queued" or r.get("refresh")]
            if not q:
                return None
            r = min(q, key=lambda r: r["added"])
            if r.get("refresh") and r["state"] == "ready":
                r["refresh"] = False              # stays "ready" (and playable) meanwhile
            else:
                r["state"], r["refresh"] = "converting", False
            self._save()
            return dict(r)

    def step(self) -> bool:
        """Convert the oldest queued score. False if there was none."""
        rec = self._next()
        if rec is None:
            return False
        sid = rec["id"]
        src = os.path.join(self.dir, sid, f"input.{rec['input']}")
        try:
            midi = self.convert(src, rec["input"], rec)
            if not midi.startswith(b"MThd"):
                raise PipelineError("the pipeline didn't return a MIDI file")
            store.write_bytes(self.midi_path(sid), trim_tail(midi))
            events, _ = smf.load(self.midi_path(sid))
            if not any(e.kind == "on" for e in events):
                raise PipelineError("the pipeline's MIDI has no notes")
            update = {"state": "ready", "error": None, "done": time.time(),
                      "duration": round(events[-1].time, 1)}
        except Exception as e:                   # one bad score mustn't stop the queue
            LOG.warning("score %s failed: %s", sid, e)
            if rec["state"] == "ready":          # fetching again: the performance it has stays
                update = {"warning": f"couldn't fetch the latest performance: {str(e)[:200]}"}
            else:
                update = {"state": "failed", "error": str(e)[:300], "done": time.time()}
        # what the pipeline noted on the record (its own id for the score, so a retry after Rich
        # chose the part in Saxy continues that score; a doubtful-reading warning)
        update = {**{k: rec[k] for k in ("pipeline_id", "warning", "warnings") if k in rec}, **update}
        with self.lock:
            if sid in self.items:                # (removed while it converted: forget it)
                self.items[sid].update(update)
                self._save()
        if update.get("state") == "ready" and self.on_ready:
            self.on_ready()
        return True

    def run(self) -> None:
        while True:
            while self.step():
                pass
            self.wake.wait(60)
            self.wake.clear()
