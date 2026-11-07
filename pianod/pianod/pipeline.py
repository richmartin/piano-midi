"""Sheet music -> a performance, by Saxy's score pipeline (saxy2's service: it reads PDFs, chooses the
piano part with us, and performs the score through Zeus). pianod sends the score, waits, and takes
the MIDI back; it never calls Zeus for this itself. Every score sent also appears in Saxy under
Rich's own scores, where a misread bar can be fixed and the piece performed again (which comes back
to pianod as a score_add under the same source ref).

Configured by SAXY_URL and SAXY_TOKEN (Saxy's PIANO_SCORES_TOKEN; from /var/lib/piano/saxy.env, never
in the repo). Unset, every conversion fails with a clear reason and the score waits to be retried.

Saxy's API (saxy2, 2026-11-07):
  POST /api/scores                  multipart: file (PDF <= 40 MB, .musicxml/.xml/.mxl <= 20 MB)
                                    + meta (JSON: title, composer, source)       -> 202 {id, state}
  GET  /api/scores/{id}             {state: reading|review|performing|ready|failed, error, parts[],
                                     pianoParts[], quality{measures, low, ...}, warnings[], ...}
  POST /api/scores/{id}/parts       {pianoParts: [...]}                          -> 202 (performing)
  GET  /api/scores/{id}/midi        the performance: one piano track, CC64/67, tempo; 409 until ready
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid

from .scores import PipelineError

SAXY_URL = os.environ.get("SAXY_URL", "").rstrip("/")
SAXY_TOKEN = os.environ.get("SAXY_TOKEN", "")
REVIEW_PAGE = os.environ.get("SAXY_REVIEW", "https://saxy2.fam-mart.in/#/scores/{id}?review=1")
ACCEPTS = ("mxl", "musicxml", "xml", "pdf")      # what Saxy reads, best first
POLL = 5.0
GIVE_UP = 45 * 60                                  # a long scan through a busy GPU, at the very worst
DOUBTFUL = 0.10                                    # share of low-confidence bars worth a warning


def ready() -> bool:
    return bool(SAXY_URL and SAXY_TOKEN)


def _request(method: str, path: str, data: bytes | None = None, ctype: str = "application/json"):
    headers = {"User-Agent": "pianod", "Authorization": f"Bearer {SAXY_TOKEN}"}
    if data is not None:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(SAXY_URL + path, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            why = json.loads(body).get("error") or body[:200].decode("utf-8", "replace")
        except ValueError:
            why = body[:200].decode("utf-8", "replace")
        if e.code == 409:
            return 409, body
        raise PipelineError(f"Saxy: {why} ({e.code})") from e
    except OSError as e:
        raise PipelineError(f"can't reach Saxy: {e}") from e


def _json(method, path, body=None):
    _, data = _request(method, path, json.dumps(body).encode() if body is not None else None)
    return json.loads(data or b"{}")


def _multipart(fields: dict, filename: str, payload: bytes) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n'
                   f'Content-Type: application/json\r\n\r\n{v}\r\n'.encode())
    out.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
               f'Content-Type: application/octet-stream\r\n\r\n'.encode() + payload + b"\r\n")
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


def choose_parts(detail: dict) -> list[str] | None:
    """The piano part(s) when the choice is clear: the one two-staff part (solo piano, or a song's
    accompaniment), or both of two (four hands). None: Rich chooses, on Saxy's review page."""
    two = [p["id"] for p in detail.get("parts") or [] if (p.get("staves") or 0) == 2]
    if len(two) in (1, 2):
        return two
    suggested = detail.get("pianoParts") or []
    return suggested if len(detail.get("parts") or []) == 1 and suggested else None


def convert(path: str, kind: str, rec: dict, sleep=time.sleep, clock=time.monotonic) -> bytes:
    if not ready():
        raise PipelineError("the score pipeline isn't connected yet (SAXY_URL/SAXY_TOKEN not set)")
    if kind not in ACCEPTS:
        raise PipelineError(f"Saxy reads PDF and MusicXML, not .{kind}")
    sid = rec.get("pipeline_id")
    if not sid:
        with open(path, "rb") as f:
            payload = f.read()
        meta = {"title": rec["title"], "composer": rec["composer"],
                "source": {k: v for k, v in {"kind": rec.get("source"), "page": rec.get("page"),
                                             "licence": rec.get("licence"), "ref": rec.get("ref")}.items() if v}}
        body, ctype = _multipart({"meta": json.dumps(meta)}, f"score.{kind}", payload)
        _, data = _request("POST", "/api/scores", body, ctype)
        sid = json.loads(data or b"{}").get("id")
        if not sid:
            raise PipelineError("Saxy didn't say which score it made")
        rec["pipeline_id"] = sid
    end = clock() + GIVE_UP
    chose = False
    while True:
        d = _json("GET", f"/api/scores/{sid}")
        state = d.get("state")
        if state == "ready":
            status, midi = _request("GET", f"/api/scores/{sid}/midi")
            if status == 200:
                q = d.get("quality") or {}
                if q.get("measures") and (q.get("low") or 0) / q["measures"] > DOUBTFUL:
                    rec["warning"] = "doubtful reading: check it in Saxy"
                rec["warnings"] = list(d.get("warnings") or [])[:10]
                return midi
        elif state == "failed":
            raise PipelineError(d.get("error") or "Saxy couldn't perform it")
        elif state == "review" and not chose:
            parts = choose_parts(d)
            if parts is None:
                raise PipelineError("choose the piano part on Saxy's page, then try again: "
                                    + REVIEW_PAGE.format(id=sid))
            _json("POST", f"/api/scores/{sid}/parts", {"pianoParts": parts})
            chose = True
        if clock() > end:
            raise PipelineError("Saxy took too long; try again later")
        sleep(POLL)
