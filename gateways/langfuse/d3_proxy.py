"""Counting reverse proxy for the D3 measurement (decision D17), not part of any benchmark run.

Sits between the Langfuse AI Gateway and Langfuse Web (the gateway's LANGFUSE_AI_GATEWAY_WEB_URL
points at it) and counts every request it forwards, by method and path. GET /__counts returns the
counts as JSON, POST /__reset zeroes them. Everything else is forwarded unchanged to UPSTREAM.
Standard library only; run by d3.py in a python:alpine container.

Environment: UPSTREAM (default http://langfuse-web:3000), PORT (default 8099).
"""

import http.client
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = urllib.parse.urlsplit(os.environ.get("UPSTREAM", "http://langfuse-web:3000"))
PORT = int(os.environ.get("PORT", "8099"))
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "host"}

lock = threading.Lock()
counts = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _reply(self, status, body, headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/__counts" and self.command == "GET":
            with lock:
                body = json.dumps(counts).encode()
            return self._reply(200, body, [("Content-Type", "application/json")])
        if path == "/__reset" and self.command == "POST":
            with lock:
                counts.clear()
            return self._reply(200, b"{}", [("Content-Type", "application/json")])
        with lock:
            key = f"{self.command} {path}"
            counts[key] = counts.get(key, 0) + 1
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        conn = http.client.HTTPConnection(UPSTREAM.hostname, UPSTREAM.port or 80, timeout=60)
        try:
            conn.request(self.command, self.path, body=data, headers=headers)
            resp = conn.getresponse()
            body = resp.read()
            out = [(k, v) for k, v in resp.getheaders() if k.lower() not in HOP and k.lower() != "content-length"]
            self._reply(resp.status, body, out)
        except OSError as e:
            self._reply(502, str(e).encode())
        finally:
            conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _forward


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
