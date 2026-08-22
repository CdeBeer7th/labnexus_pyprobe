"""A tiny stand-in for the LabNexus server, so tests exercise real HTTP."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

TOKEN = "test-token"
VALID = {"me@lab.org": "hunter2"}


class Handler(BaseHTTPRequestHandler):
    uploads: list[bytes] = []

    def log_message(self, *args):  # silence the default stderr spam
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path == "/auth/jwt/login":
            fields = dict(pair.split("=", 1) for pair in body.decode().split("&") if "=" in pair)
            user = fields.get("username", "").replace("%40", "@")
            if VALID.get(user) == fields.get("password"):
                return self._json(200, {"access_token": TOKEN})
            return self._json(400, {"detail": "LOGIN_BAD_CREDENTIALS"})

        if self.path == "/files/upload/pyprobe":
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._json(401, {"detail": "Unauthorized"})
            Handler.uploads.append(body)
            return self._json(200, {"ok": True})

        return self._json(404, {"detail": "Not Found"})


@pytest.fixture
def server():
    Handler.uploads = []
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def uploads():
    return Handler.uploads
