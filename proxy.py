#!/usr/bin/env python3
"""
Minimal local proxy for the NVIDIA chat API.

Why this exists: browsers block direct JS calls to integrate.api.nvidia.com
(CORS). This script runs on YOUR machine, holds the API key, and forwards
requests from the HTML page to NVIDIA, streaming the response back.

Usage:
    python3 proxy.py

Then open my-ai-chat.html in your browser — it's already pointed at
http://127.0.0.1:8787/chat.

Only standard library is used, so no pip install is needed.
"""

import json
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- Config ---------------------------------------------------------------
import os
API_KEY = os.environ.get("NVIDIA_API_KEY")
if not API_KEY:
    raise SystemExit(
        "Missing NVIDIA_API_KEY environment variable.\n"
        "In a GitHub Codespace: repo/org Settings -> Secrets and variables -> "
        "Codespaces -> New secret, name it NVIDIA_API_KEY. Rebuild/restart the "
        "codespace so it's injected, then re-run this script."
    )
NVIDIA_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8787
# ---------------------------------------------------------------------------


class ProxyHandler(BaseHTTPRequestHandler):
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        # Preflight request from the browser
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path != "/chat":
            self.send_response(404)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b"Not found. POST to /chat.")
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            incoming = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b"Invalid JSON body.")
            return

        # Force stream=True so we can relay chunks as they arrive
        incoming["stream"] = True

        req = urllib.request.Request(
            NVIDIA_ENDPOINT,
            data=json.dumps(incoming).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

        try:
            upstream = urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self._cors_headers()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(e.read())
            return
        except Exception as e:
            self.send_response(502)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(f"Proxy error: {e}".encode("utf-8"))
            return

        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        # Relay the streamed response chunk by chunk
        try:
            while True:
                chunk = upstream.read(1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client closed the tab/request early — fine
        finally:
            upstream.close()

    def log_message(self, format, *args):
        # Quieter console output; comment this out if you want full request logs
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    print(f"Proxy running at http://{LISTEN_HOST}:{LISTEN_PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
