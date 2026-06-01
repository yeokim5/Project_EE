"""Local fallback API server for the web UI.

Vercel-native Python remains in api/*.py. This server exposes the same payload
shapes at /api/process, /api/extract, and /api/export for local development or a
split frontend/backend deployment if Vercel's mixed Next.js + Python routing is
unavailable. Point the frontend at it with PYTHON_API_BASE_URL (see
next.config.ts), then run `npm run dev` alongside `npm run dev:api`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.export import _export  # noqa: E402
from api.extract import _process as _extract  # noqa: E402
from api.process import _process  # noqa: E402

ROUTES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "/api/process": _process,
    "/api/extract": _extract,
    "/api/export": _export,
}


class WebApiHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self._send_empty(204)

    def do_POST(self) -> None:
        route = ROUTES.get(self.path)
        if route is None:
            self._send_json({"ok": False, "error": "Not found"}, status=404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            response = route(payload)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        self._send_json(response)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), WebApiHandler)
    base = f"http://{args.host}:{args.port}"
    print(f"Serving web API at {base} ({', '.join(sorted(ROUTES))})")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
