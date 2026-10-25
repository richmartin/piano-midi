"""The local mirror of the MIDI library.

The server (regenerate_library.py's output) is the librarian; pianod keeps a full copy — the
whole library is ~14 MB — so browsing and playing never touch the network. sync() refreshes the
mirror from the server: library.json first, then any file the mirror is missing. Files are
immutable by id (file-id-000123.mid), so "missing" is the only check needed.

Layout under root/:  library.json, midi-files/file-id-*.mid, recordings/*.mid (our own takes).
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request

from . import store

LOG = logging.getLogger("pianod")


def _default_fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


class Library:
    def __init__(self, root: str):
        self.root = root
        self.composers: list[dict] = []
        self._works: dict[str, dict] = {}       # id -> {id, title, url, composer}
        os.makedirs(os.path.join(root, "midi-files"), exist_ok=True)
        os.makedirs(os.path.join(root, "recordings"), exist_ok=True)
        self.load()

    def load(self) -> None:
        data = store.read_json(os.path.join(self.root, "library.json"), {"composers": []})
        self.composers = data.get("composers", [])
        self._works = {}
        for c in self.composers:
            for w in c.get("works", []):
                self._works[w["id"]] = {**w, "composer": c["name"]}

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
                return [{**w, "composer": c["name"]} for w in c.get("works", [])]
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
