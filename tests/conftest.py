"""A tiny stand-in for the LabNexus server, so tests exercise real HTTP."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

TOKEN = "test-token"
VALID = {"me@lab.org": "hunter2"}
WORKSPACE_ID = "11111111-2222-3333-4444-555555555555"

#: Real vendor exports, borrowed from the shared parser package so pyProbe's
#: tests parse the same files the parsers are themselves tested against.
FIXTURES = (
    Path(__file__).resolve().parents[2] / "labnexus_plate_parsers" / "tests" / "fixtures"
)


@pytest.fixture
def spark_export() -> Path:
    path = FIXTURES / "tecan_spark.xlsx"
    if not path.exists():
        pytest.skip(f"shared parser fixtures not available at {FIXTURES}")
    return path


class Handler(BaseHTTPRequestHandler):
    uploads: list[bytes] = []
    spectrometer_uploads: list[dict] = []

    def log_message(self, *args):  # silence the default stderr spam
        pass

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def do_GET(self):
        if not self._authed():
            return self._json(401, {"detail": "Unauthorized"})

        if self.path == "/api/workspaces/":
            return self._json(
                200,
                [
                    {"id": WORKSPACE_ID, "name": "Kinetics", "owned": True},
                    {
                        "id": "99999999-0000-0000-0000-000000000000",
                        "name": "Shared",
                        "owned": False,
                    },
                ],
            )
        if self.path == "/api/files/spectrometer/models":
            return self._json(200, {"parser_version": "0.1.0", "models": []})
        return self._json(404, {"detail": "Not Found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path == "/auth/jwt/login":
            fields = dict(pair.split("=", 1) for pair in body.decode().split("&") if "=" in pair)
            user = fields.get("username", "").replace("%40", "@")
            if VALID.get(user) == fields.get("password"):
                return self._json(200, {"access_token": TOKEN})
            return self._json(400, {"detail": "LOGIN_BAD_CREDENTIALS"})

        if not self._authed():
            return self._json(401, {"detail": "Unauthorized"})

        if self.path == "/api/files/upload/pyprobe":
            Handler.uploads.append(body)
            return self._json(200, {"ok": True})

        if self.path.startswith("/api/files/spectrometer/upload"):
            # Pull the structured payload back out of the multipart body so
            # tests can assert on what the client actually parsed and sent.
            text = body.decode("utf-8", errors="replace")
            structured = None
            marker = 'name="structured"'
            if marker in text:
                after = text.split(marker, 1)[1]
                structured = after.split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0]
            Handler.spectrometer_uploads.append(
                {
                    "query": self.path.split("?", 1)[1] if "?" in self.path else "",
                    "structured": structured,
                    "raw": body,
                }
            )
            return self._json(
                200,
                {
                    "file_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "data": json.loads(structured) if structured else {},
                },
            )

        return self._json(404, {"detail": "Not Found"})


@pytest.fixture
def server():
    Handler.uploads = []
    Handler.spectrometer_uploads = []
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def uploads():
    return Handler.uploads


@pytest.fixture
def spectrometer_uploads():
    return Handler.spectrometer_uploads
