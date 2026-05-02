"""Serve the CourtVision static dashboard against the repo root.

Usage:
    python scripts/serve_dashboard.py            # http://localhost:8765/dashboard/
    python scripts/serve_dashboard.py --port 9000

The server roots at the repository root so dashboard/index.html can fetch
relative paths into ../outputs/runtime/... and ../data/history/... .
"""
from __future__ import annotations

import argparse
import http.server
import os
import socketserver
import sys
import webbrowser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files with cache disabled so live-edited CSV/JSON show immediately."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format, *args):  # noqa: A002 - matches stdlib signature
        sys.stderr.write("[serve] %s - %s\n" % (self.address_string(), format % args))


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the CourtVision dashboard.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open the browser.")
    args = parser.parse_args()

    dashboard_dir = REPO_ROOT / "dashboard"
    if not dashboard_dir.exists():
        sys.stderr.write(f"error: dashboard/ folder not found at {dashboard_dir}\n")
        return 2

    os.chdir(REPO_ROOT)
    url = f"http://{args.host}:{args.port}/dashboard/"

    sys.stderr.write(f"[serve] Serving {REPO_ROOT} at {url}\n")
    sys.stderr.write(f"[serve] dashboard reads ../outputs/runtime/ and ../data/history/\n")

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((args.host, args.port), NoCacheHandler) as httpd:
        if not args.no_open:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            sys.stderr.write("\n[serve] stopped\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
