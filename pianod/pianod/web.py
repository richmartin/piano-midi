"""pianod over HTTP, for the web player on piano.fam-mart.in (murdoch proxies /api/ here).

  GET  /api/state           the state, as the panel gets it
  GET  /api/events          server-sent events: every state push, and the keys as they move
  POST /api/<cmd>           a command, its arguments as a JSON object; the reply as the socket's
  GET  /api/clock           {t}: pianod's clock, wall time in seconds, for POST /api/events
  POST /api/events          {source, events: [{t, note, vel, dur} | {t, cc, value}], dryRun}
                            -> {accepted, late, now, horizon}: notes to sound at t (live.py)
  POST /api/events/cancel   {source}: everything pending from it, and its sounding notes, now

Saxy (CORS_ORIGINS) may call it from the browser: its Band plays the piano part on the piano.

Only the library and playback: WiFi, calibration and Performance mode stay on the panel, which
is at the piano. LAN only, like everything else here; no authentication.

With PIANO_WEB set to a directory (the generator's output), the web player is served from here
too: at http://<pi>:8080/ it works with no murdoch at all, reading the Pi's own mirror.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

LOG = logging.getLogger("pianod")

PORT = int(os.environ.get("PIANO_HTTP", "8080"))
WEB_ROOT = os.environ.get("PIANO_WEB", "")
COMMANDS = {"status", "play", "toggle", "pause", "resume", "stop", "next", "prev", "seek", "volume",
            "panic", "fav", "favourites", "queue", "shelves", "collection", "sync"}
# POST paths that aren't a command's own name
ROUTES = {"events": "live", "events/cancel": "live_cancel"}
CORS_ORIGINS = set(filter(None, os.environ.get(
    "PIANO_CORS", "https://saxy2.fam-mart.in").split(",")))
HEARTBEAT = 15.0              # seconds: keeps proxies from closing a quiet event stream
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".mid": "audio/midi",
         ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2"}


def make_handler(d, data_dir: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "pianod"

        def log_message(self, fmt, *args):          # quiet: the journal is in RAM
            LOG.debug("http %s", fmt % args)

        def _cors(self):
            origin = self.headers.get("Origin")
            if origin in CORS_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def do_OPTIONS(self):                       # the browser's preflight for a JSON POST
            self.send_response(204)
            self._cors()
            if self.headers.get("Origin") in CORS_ORIGINS:
                self.send_header("Access-Control-Allow-Methods", "GET, POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _json(self, obj, status=200):
            body = json.dumps(obj, separators=(",", ":")).encode()
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/clock":
                return self._json({"t": d.live.now()})
            if path == "/api/state":
                return self._json(d.state())
            if path == "/api/events":
                return self._events()
            if WEB_ROOT:
                return self._static(path)
            self._json({"ok": False, "error": "not found"}, 404)

        def do_POST(self):
            path = urlsplit(self.path).path
            cmd = path[len("/api/"):] if path.startswith("/api/") else ""
            cmd = ROUTES.get(cmd, cmd)
            if cmd not in COMMANDS and cmd not in ROUTES.values():
                return self._json({"ok": False, "error": f"not here: {cmd!r}"}, 404)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                args = json.loads(self.rfile.read(n) or b"{}") if n else {}
                if not isinstance(args, dict):
                    raise ValueError("arguments must be an object")
            except ValueError as e:
                return self._json({"ok": False, "error": f"bad request: {e}"}, 400)
            self._json(d.handle({**args, "cmd": cmd}))

        def _events(self):
            q: queue.Queue = queue.Queue(maxsize=200)

            def listen(msg):
                try:
                    q.put_nowait(msg)
                except queue.Full:             # a stalled browser: drop, it gets the next state
                    pass
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            d.listeners.append(listen)
            try:
                self._send_event(d.state())
                while True:
                    try:
                        self._send_event(q.get(timeout=HEARTBEAT))
                    except queue.Empty:
                        self.wfile.write(b": still here\n\n")
                        self.wfile.flush()
            except (OSError, ValueError):
                pass                            # the browser went away
            finally:
                if listen in d.listeners:
                    d.listeners.remove(listen)

        def _send_event(self, msg):
            self.wfile.write(b"data: " + json.dumps(msg, separators=(",", ":")).encode() + b"\n\n")
            self.wfile.flush()

        def _static(self, path):
            # library.json and the MIDI files are the Pi's mirror; the rest is the web player.
            rel = unquote(path).lstrip("/") or "index.html"
            base = data_dir if (rel == "library.json" or rel.startswith("midi-files/")) else WEB_ROOT
            full = os.path.realpath(os.path.join(base, rel))
            if not full.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(full):
                return self._json({"ok": False, "error": "not found"}, 404)
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(d, data_dir: str, port: int = PORT) -> ThreadingHTTPServer | None:
    """Start serving in a thread. None if the port can't be had (pianod runs on without it)."""
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(d, data_dir))
    except OSError as e:
        LOG.warning("no web API on port %d: %s", port, e)
        return None
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True, name="web").start()
    LOG.info("web API on port %d%s", port, f", serving {WEB_ROOT}" if WEB_ROOT else "")
    return srv
