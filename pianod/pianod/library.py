"""The local mirror of the MIDI library.

The server (regenerate_library.py's output) is the librarian; pianod keeps a full copy — the
whole library is ~14 MB — so browsing and playing never touch the network. sync() refreshes the
mirror from the server: library.json first, then any file the mirror is missing. Files are
immutable by id (file-id-000123.mid), so "missing" is the only check needed.

Layout under root/:  library.json, midi-files/file-id-*.mid, recordings/*.mid (our own takes),
durations.json (each work's length, measured once, in the background: on a Pi 4 the whole library
takes minutes, longer than systemd waits for pianod to start).

The catalogue is tidied (catalogue.tidy: canonical composers, eras, pianists, roll systems) when
it's loaded, whether or not the server already did: a v1 library.json from an old server still
browses by era and pianist.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request

from . import smf, store

LOG = logging.getLogger("pianod")


def _default_fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


COLLECTIONS = [
    # slug, name, rule on a work (its clean title and movement, its length, its pianist)
    ("short", "Short pieces", lambda w, text: 0 < (w.get("duration") or 0) < 180),
    ("quiet", "Nocturnes and slow pieces", lambda w, text: re.search(
        r"nocturne|berceuse|lullaby|r[eê]verie|consolation|romance|el[eé]gie|elegy|adagio|andante|"
        r"largo|lento|song|lied|barcarolle|pastoral|dream|tr[aä]umerei|clair de lune|serenade|"
        r"gymnop|gnossienne|meditation|intermezzo", text)),
    ("dances", "Dances and rags", lambda w, text: re.search(
        r"waltz|valse|mazurka|polonaise|polka|\brag\b|ragtime|tango|habanera|minuet|menuet|gavotte|"
        r"tarantell|bolero|danc|danse|march|jota|fandango|czardas|csardas|[eé]cossaise|l[aä]ndler|"
        r"two-step|cakewalk|galop|sarabande|bourr[eé]e|gigue|allemande|courante", text)),
    ("showpieces", "Showpieces and études", lambda w, text: re.search(
        r"[eé]tude|study|rhapsod|toccata|scherzo|campanella|fantasi|fantasy|paraphrase|"
        r"transcription|caprice|capriccio|variations|feux|fireworks|islamey|mephisto|presto", text)),
    ("sonatas", "Sonatas", lambda w, text: "sonat" in text),
    ("rolls", "Great pianists, on rolls", lambda w, text: bool(w.get("performer"))),
    ("long", "Long listens", lambda w, text: (w.get("duration") or 0) >= 600),
]


class Library:
    def __init__(self, root: str):
        self.root = root
        self.composers: list[dict] = []
        self.pianists: list[dict] = []
        self._works: dict[str, dict] = {}       # id -> the work, with its composer's names
        os.makedirs(os.path.join(root, "midi-files"), exist_ok=True)
        os.makedirs(os.path.join(root, "recordings"), exist_ok=True)
        self.load()

    def load(self) -> None:
        data = store.read_json(os.path.join(self.root, "library.json"), {"composers": []})
        try:
            from . import catalogue
            known = store.read_json(os.path.join(self.root, "durations.json"), {})
            data = catalogue.tidy(data, duration=known.get)
        except ImportError:
            pass
        except Exception:                     # a bad catalogue must never stop the piano playing
            LOG.exception("couldn't tidy the catalogue; using it as it is")
        self.composers = data.get("composers", [])
        self.pianists = data.get("pianists", [])
        names = {p["slug"]: p for p in self.pianists}
        self._works = {}
        for c in self.composers:
            for w in c.get("works", []):
                p = names.get(w.get("performer") or "")
                self._works[w["id"]] = {**w, "composer": c["name"], "composer_short": c.get("short") or c["name"],
                                        "composer_slug": c.get("slug"),
                                        "performer_name": p["name"] if p else None,
                                        "performer_short": p.get("short") if p else None}

    def measure(self, pause: float = 0.02, save_every: int = 25) -> int:
        """Measure the works with no length yet, keeping them in durations.json as it goes (so a
        restart part way through loses little), then reload. Slow: run it off the main thread.
        Returns how many were measured."""
        path = os.path.join(self.root, "durations.json")
        known = store.read_json(path, {})
        todo = [w for w in list(self._works.values()) if w["id"] not in known and not w.get("duration")]
        n = 0
        for w in todo:
            try:
                events, _ = smf.load(self.path(w))
            except Exception:               # an unreadable file has no length; it mustn't stop the rest
                continue
            known[w["id"]] = round(events[-1].time, 1) if events else 0.0
            n += 1
            if n % save_every == 0:
                store.write_json(path, known)
            time.sleep(pause)               # leave the CPU to playback
        if n:
            store.write_json(path, known)
            self.load()
            LOG.info("measured %d works' lengths", n)
        return n

    # --- browsing ---------------------------------------------------------------------------
    def composer_list(self) -> list[dict]:
        return [{k: c.get(k) for k in ("name", "slug", "short", "first", "sort", "born", "died", "era")}
                | {"works": len(c.get("works", []))} for c in self.composers]

    def pianist_list(self) -> list[dict]:
        return [{k: p.get(k) for k in ("name", "slug", "short", "born", "died")} | {"works": len(p.get("works", []))}
                for p in sorted(self.pianists, key=lambda p: -len(p.get("works", [])))]

    def pianist_works(self, slug: str) -> list[dict]:
        for p in self.pianists:
            if p["slug"] == slug:
                return [self._works[i] for i in p.get("works", []) if i in self._works]
        raise ValueError(f"no such pianist {slug!r}")

    def collections(self) -> list[dict]:
        return [{"slug": slug, "name": name, "count": len(self.collection(slug))}
                for slug, name, _ in COLLECTIONS if self.collection(slug)]

    def collection(self, slug: str) -> list[dict]:
        rule = next((r for s, _, r in COLLECTIONS if s == slug), None)
        if rule is None:
            raise ValueError(f"no such collection {slug!r}")
        out = [w for w in self._works.values()
               if rule(w, (w["title"] + " " + (w.get("original") or "")).lower())]
        return sorted(out, key=lambda w: (w.get("composer_short") or "", w["title"]))

    # --- lookups ------------------------------------------------------------------------
    def work(self, work_id: str) -> dict:
        w = self._works.get(work_id)
        if w is None and work_id.startswith("recording:"):
            w = next((r for r in self.recordings() if r["id"] == work_id), None)
        if w is None:
            raise ValueError(f"no such work {work_id!r}")
        return w

    def works_of(self, slug: str) -> list[dict]:
        for c in self.composers:
            if c.get("slug") == slug:
                return [self._works[w["id"]] for w in c.get("works", [])]
        raise ValueError(f"no such composer {slug!r}")

    def all_works(self) -> list[dict]:
        return list(self._works.values())

    def path(self, work: dict) -> str:
        """The local file for a work; its url is a site path like /midi-files/file-id-000001.mid."""
        name = os.path.basename(urllib.parse.urlparse(work["url"]).path)
        if not re.fullmatch(r"[\w. -]+\.mid", name):
            raise ValueError(f"suspicious file name {name!r} in {work.get('id')}")
        sub = "recordings" if work["url"].startswith("/recordings/") else "midi-files"
        return os.path.join(self.root, sub, name)

    def missing(self) -> list[dict]:
        return [w for w in self._works.values() if not os.path.exists(self.path(w))]

    # --- sync ---------------------------------------------------------------------------
    def sync(self, base_url: str, fetch=_default_fetch) -> dict:
        """Refresh the mirror from the server. Returns {"works": n, "fetched": n}.

        The new library.json only replaces the old one after it parses and its shape checks out,
        and each file lands atomically, so a failed sync never breaks what was there.
        """
        raw = fetch(base_url.rstrip("/") + "/library.json")
        data = json.loads(raw)
        if not isinstance(data.get("composers"), list):
            raise ValueError("library.json has no composers list")
        for c in data["composers"]:
            if not (c.get("name") and c.get("slug") is not None and isinstance(c.get("works"), list)):
                raise ValueError(f"malformed composer entry: {str(c)[:80]}")
            for w in c["works"]:
                if not (w.get("id") and w.get("title") and w.get("url")):
                    raise ValueError(f"malformed work entry: {str(w)[:80]}")
        store.write_bytes(os.path.join(self.root, "library.json"), raw)
        self.load()
        fetched = 0
        for w in self.missing():
            url = base_url.rstrip("/") + w["url"]
            try:
                store.write_bytes(self.path(w), fetch(url))
                fetched += 1
            except (OSError, ValueError) as e:
                LOG.warning("sync: %s: %s", url, e)
        return {"works": len(self._works), "fetched": fetched}

    # --- recordings ----------------------------------------------------------------------
    def recording_path(self, title: str) -> str:
        safe = re.sub(r"[^\w -]+", "", title).strip() or "untitled"
        path = os.path.join(self.root, "recordings", f"{safe}.mid")
        n = 2
        while os.path.exists(path):
            path = os.path.join(self.root, "recordings", f"{safe}-{n}.mid")
            n += 1
        return path

    def recordings(self) -> list[dict]:
        d = os.path.join(self.root, "recordings")
        out = []
        for name in sorted(os.listdir(d)):
            if name.endswith(".mid"):
                out.append({"id": f"recording:{name}", "title": name[:-4],
                            "url": f"/recordings/{name}", "composer": "Recordings"})
        return out
