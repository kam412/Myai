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

# Map each model name to the Codespaces secret holding its API key.
# Add a new line here any time you wire up another model.
MODEL_KEY_MAP = {
    "deepseek-ai/deepseek-v4-pro-0813": "NVIDIA_KEY_DEEPSEEK",
    "moonshotai/kimi-k3": "NVIDIA_KEY_KIMI",
    "nvidia/nemotron-3.5-lightning-30b-a3b": "NVIDIA_KEY_NEMOTRON",
}

# Resolve all keys up front so a missing secret fails fast and clearly,
# instead of silently 500-ing later on first use.
MODEL_KEYS = {}
missing = []
for model, secret_name in MODEL_KEY_MAP.items():
    value = os.environ.get(secret_name)
    if value:
        MODEL_KEYS[model] = value
    else:
        missing.append(secret_name)

if missing:
    print(
        "Warning: missing Codespaces secret(s) for: " + ", ".join(missing) + "\n"
        "Those models will return an error until the secret is added and the "
        "codespace is restarted. Other configured models will still work."
    )

if not MODEL_KEYS:
    raise SystemExit(
        "No API keys were loaded at all. Add at least one secret from "
        "MODEL_KEY_MAP above in repo Settings -> Secrets and variables -> "
        "Codespaces, then restart the codespace and re-run this script."
    )

NVIDIA_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
LISTEN_HOST = "0.0.0.0"
# Render (and similar hosts) assign a port dynamically via $PORT.
# Falls back to 8787 for local/Codespaces use where that's not set.
LISTEN_PORT = int(os.environ.get("PORT", 8787))
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

    def do_GET(self):
        # Lightweight endpoint for an external uptime monitor to ping.
        # This is what keeps a free Render instance from going to sleep --
        # point a service like UptimeRobot at https://your-app.onrender.com/ping
        if self.path == "/ping":
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b"Not found. POST to /chat, or GET /ping.")

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

        model = incoming.get("model")
        if model not in MODEL_KEY_MAP:
            self.send_response(400)
            self._cors_headers()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                f"Unknown model '{model}'. Add it to MODEL_KEY_MAP in proxy.py "
                f"with the Codespaces secret name that holds its key.".encode("utf-8")
            )
            return

        api_key = MODEL_KEYS.get(model)
        if not api_key:
            secret_name = MODEL_KEY_MAP[model]
            self.send_response(500)
            self._cors_headers()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                f"Missing API key for model '{model}'. Add the secret "
                f"{secret_name} in Codespaces settings and restart the "
                f"codespace.".encode("utf-8")
            )
            return

        req = urllib.request.Request(
            NVIDIA_ENDPOINT,
            data=json.dumps(incoming).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
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