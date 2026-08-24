"""Zero-dependency HTTP server for the dashboard.

Standard library only: no pip install, no build step, no lockfile. The whole
demo is `python3 -m riskmemory.server` on any machine with Python 3.11+.
"""
from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .app import App

mimetypes.add_type("image/webp", ".webp")

WEB = Path(__file__).resolve().parent.parent / "web"
STATE = App()
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "MerchantRiskMemory/1.0"

    def log_message(self, fmt, *args):        # keep the console readable
        pass

    # -- helpers ------------------------------------------------------------
    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB / rel).resolve()
        if not str(target).startswith(str(WEB.resolve())) or not target.is_file():
            self.send_error(404, "Not found")
            return
        body = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routes -------------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._static(path)

        with LOCK:
            if path == "/api/overview":
                return self._json(STATE.overview())
            if path == "/api/directory":
                qs = parse_qs(urlparse(self.path).query)
                return self._json(STATE.directory(
                    q=(qs.get("q") or [""])[0], band=(qs.get("band") or [""])[0],
                    limit=int((qs.get("limit") or ["40"])[0]),
                    offset=int((qs.get("offset") or ["0"])[0])))
            if path == "/api/dashboard":
                return self._json(STATE.dashboard())
            if path == "/api/summary":
                return self._json(STATE.summary())
            if path == "/api/portfolio":
                return self._json(STATE.portfolio())
            if path == "/api/queue":
                return self._json(STATE.queue())
            if path.startswith("/api/brief/"):
                brief = STATE.brief(path.rsplit("/", 1)[-1])
                return self._json(brief or {"error": "not found"},
                                  200 if brief else 404)
            if path == "/api/alerts":
                return self._json([a.to_dict() for a in STATE.alerts()])
            if path == "/api/drift":
                return self._json(STATE.drift())
            if path == "/api/memory":
                return self._json(STATE.memory_list())
            if path == "/api/transcript":
                return self._json(STATE.transcript())
            if path == "/api/gate-history":
                return self._json(STATE.gate_history)
            if path == "/api/incidents":
                out = [{
                    "id": m.id, "name": m.name,
                    "category": m.truth_category, "note": m.truth_note,
                    "status": m.status, "scenario": bool(m.scenario),
                } for m in STATE.merchants
                    if m.truth_bad and m.status in ("approved", "terminated")]
                # Named narrative cases first: they carry a real incident note,
                # so the dropdown opens on something worth demonstrating rather
                # than an anonymous row from the background population.
                out.sort(key=lambda d: (not d["scenario"], d["category"] or ""))
                return self._json(out[:40])
        self.send_error(404, "Unknown endpoint")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        payload = self._body()
        with LOCK:
            if path == "/api/decide":
                return self._json(STATE.record_decision(
                    payload.get("merchant_id", ""), payload.get("action", ""),
                    payload.get("rationale", "")))
            if path == "/api/ingest":
                return self._json(STATE.ingest_incident(payload.get("merchant_id", "")))
            if path == "/api/ask":
                return self._json(STATE.ask(payload.get("text", "")))
            if path == "/api/replay":
                return self._json(STATE.replay_now())
            if path == "/api/reset":
                STATE.reset()
                return self._json({"ok": True})
        self.send_error(404, "Unknown endpoint")


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    p = STATE.portfolio()
    print("\n  Merchant Risk Memory")
    print(f"  {p['approved']:,} active merchants  |  {p['queue_size']} in the review queue"
          f"  |  {p['alert_total']} open alerts")
    print(f"  graph: {p['graph']['nodes']:,} nodes, {p['graph']['edges']:,} edges"
          f"  |  memory: {p['memory']['active']} active records")
    print(f"\n  -> {url}\n  Ctrl-C to stop\n")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped\n")
        httpd.shutdown()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Merchant Risk Memory demo server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    serve(a.host, a.port, not a.no_browser)
