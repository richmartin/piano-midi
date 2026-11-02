"""What was played, and how much of it: for the companion ("you liked X last week").

The daemon ticks this with its state once a second; no hooks into the many ways a piece starts
or stops. A listen begins when something plays that isn't the listen in hand (another work, or
the same one started again), and is written when it ends: another listen begins, or nothing has
played for IDLE seconds. One JSON row per listen, appended to DATA/history.jsonl:

  {"t": wall time it began, "id": work, "title", "composer", "len": seconds,
   "heard": the furthest point reached, seconds, "finished": heard to (near) the end}
"""

from __future__ import annotations

import json
import logging
import os
import time

LOG = logging.getLogger("pianod")

IDLE = 120.0            # stopped or paused this long: the listen is over
END_SLACK = 5.0         # within this of the end counts as finished
RESTART = 5.0           # the position going back further than this is a fresh listen


class History:
    def __init__(self, root: str, wall=time.time):
        self.path = os.path.join(root, "history.jsonl")
        self.wall = wall
        self.cur: dict | None = None
        self.idle_since: float | None = None

    def tick(self, st: dict, now: float) -> None:
        """st: the daemon's state (mode, playing, id, title, composer, pos, len)."""
        playing = st.get("mode") == "library" and st.get("playing") and st.get("id")
        if playing:
            self.idle_since = None
            cur = self.cur
            if cur is None or cur["id"] != st["id"] or st.get("pos", 0) + RESTART < cur["heard"]:
                self._close()
                self.cur = {"t": round(self.wall() - st.get("pos", 0), 1), "id": st["id"],
                            "title": st.get("title"), "composer": st.get("composer"),
                            "len": st.get("len") or 0, "heard": 0.0}
            self.cur["heard"] = max(self.cur["heard"], st.get("pos") or 0)
        elif self.cur is not None:
            if self.idle_since is None:
                self.idle_since = now
            elif now - self.idle_since >= IDLE:
                self._close()

    def _close(self) -> None:
        cur, self.cur = self.cur, None
        if cur is None or cur["heard"] < 1:
            return
        cur["heard"] = round(cur["heard"], 1)
        cur["finished"] = bool(cur["len"]) and cur["heard"] >= cur["len"] - END_SLACK
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(cur, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError as e:
            LOG.warning("history: can't write (%s)", e)

    def flush(self) -> None:
        self._close()

    def read(self, limit: int = 200) -> list[dict]:
        """The latest listens, newest first, with the one in hand (if any) on top."""
        rows = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
        rows = rows[-limit:][::-1]
        if self.cur is not None:
            rows.insert(0, {**self.cur, "finished": False, "now": True})
        return rows[:limit]
