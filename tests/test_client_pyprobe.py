"""The pyProbe token flow: exchanging email + token for an unattended session.

Exercised against the fake server in ``conftest.py`` so the real HTTP shape —
JSON body in, bearer session out — is what gets tested.
"""

from __future__ import annotations

import pytest
from conftest import (
    PYPROBE_TOKEN,
    PYPROBE_WORKSPACE_ID,
    PYPROBE_WORKSPACE_NAME,
    TOKEN,
)
from labnexus_plate_parsers import SpectrometerModel

from labnexus_pyprobe.client import AuthError, LabNexusClient
from labnexus_pyprobe.config import normalise_server


def make_client(server: str) -> LabNexusClient:
    return LabNexusClient(normalise_server(server, "http", allow_http=True))


class TestTokenExchange:
    def test_opens_a_session(self, server):
        client = make_client(server)

        who = client.authenticate_pyprobe("me@lab.org", PYPROBE_TOKEN)

        assert who == "me@lab.org"
        assert client.logged_in
        assert client.token == TOKEN
        assert client.session.headers["Authorization"] == f"Bearer {TOKEN}"

    def test_remembers_the_default_workspace(self, server):
        """So the bench client can say where runs will land, without a lookup."""
        client = make_client(server)

        client.authenticate_pyprobe("me@lab.org", PYPROBE_TOKEN)

        assert client.default_workspace_id == PYPROBE_WORKSPACE_ID
        assert client.default_workspace_name == PYPROBE_WORKSPACE_NAME

    def test_uses_the_token_endpoint_and_nothing_else(self, server, requests_seen):
        """The exchange is one POST to /auth/pyprobe/token - no password login.

        Pinned on the wire rather than by reading the code: a regression that
        quietly fell back to /auth/jwt/login would still "work" against a
        server that accepts both, and only fail on an account with a CAPTCHA
        or a second factor - which is exactly who uses a token.
        """
        client = make_client(server)

        client.authenticate_pyprobe("me@lab.org", PYPROBE_TOKEN)

        assert requests_seen == [("POST", "/auth/pyprobe/token")]

    def test_sends_the_email_and_token_as_json(self, server):
        """The body shape the server's PyProbeAuthRequest model expects."""
        client = make_client(server)
        sent = {}

        original = client.session.post

        def capture(url, **kwargs):
            sent.update(url=url, json=kwargs.get("json"), data=kwargs.get("data"))
            return original(url, **kwargs)

        client.session.post = capture
        client.authenticate_pyprobe("me@lab.org", PYPROBE_TOKEN)

        assert sent["url"].endswith("/auth/pyprobe/token")
        assert sent["json"] == {"email": "me@lab.org", "token": PYPROBE_TOKEN}
        assert sent["data"] is None  # JSON, not a form post

    def test_rejects_a_bad_token(self, server):
        client = make_client(server)

        with pytest.raises(AuthError, match="rejected"):
            client.authenticate_pyprobe("me@lab.org", "lnxp_wrong")

        assert not client.logged_in

    def test_rejects_the_wrong_email(self, server):
        """The token alone is not a credential — the account has to match."""
        client = make_client(server)

        with pytest.raises(AuthError, match="rejected"):
            client.authenticate_pyprobe("someone.else@lab.org", PYPROBE_TOKEN)

    def test_unreachable_server_is_an_auth_error(self):
        client = make_client("127.0.0.1:1")

        with pytest.raises(AuthError, match="Could not reach"):
            client.authenticate_pyprobe("me@lab.org", PYPROBE_TOKEN)

    def test_the_session_can_upload(self, server, tmp_path, spark_export, spectrometer_uploads):
        """The whole point: a token session is accepted by the upload route."""
        client = make_client(server)
        client.authenticate_pyprobe("me@lab.org", PYPROBE_TOKEN)
        target = tmp_path / "run.xlsx"
        target.write_bytes(spark_export.read_bytes())

        client.upload_spectrometer(target, SpectrometerModel.tecanSpark)

        assert len(spectrometer_uploads) == 1
        assert "workspace_id" not in spectrometer_uploads[0]["query"]

    def test_an_explicit_workspace_still_wins(
        self, server, tmp_path, spark_export, spectrometer_uploads
    ):
        client = make_client(server)
        client.authenticate_pyprobe("me@lab.org", PYPROBE_TOKEN)
        target = tmp_path / "run.xlsx"
        target.write_bytes(spark_export.read_bytes())

        client.upload_spectrometer(target, SpectrometerModel.tecanSpark, "WS-CHOSEN")

        assert "workspace_id=WS-CHOSEN" in spectrometer_uploads[0]["query"]

    def test_no_captcha_is_solved(self, server, monkeypatch):
        """A bench has no browser, so this path must never reach the CAPTCHA."""
        client = make_client(server)

        def explode() -> None:
            raise AssertionError("the pyProbe token path must not solve a CAPTCHA")

        monkeypatch.setattr(client, "solve_captcha", explode)
        client.authenticate_pyprobe("me@lab.org", PYPROBE_TOKEN)

        assert client.logged_in
