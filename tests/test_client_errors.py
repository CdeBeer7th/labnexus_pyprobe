"""Turning a failed connection into something the user can act on.

"Max retries exceeded" is what requests says when it gives up; it never says
why. These pin the translation, including the two failures a bench actually
hits: an https client pointed at a plain http server, and an untrusted
certificate.
"""

from __future__ import annotations

import socket

import pytest
import requests
from conftest import PYPROBE_TOKEN

from labnexus_pyprobe.client import AuthError, LabNexusClient, describe_unreachable

URL = "https://lab.example.com:8000"


class TestDescribeUnreachable:
    def test_a_failed_handshake_points_at_plain_http(self):
        """The exact shape requests raises for https against an http port."""
        exc = requests.exceptions.SSLError(
            "HTTPSConnectionPool(host='lab', port=8000): Max retries exceeded with "
            "url: /auth/pyprobe/token (Caused by SSLError(SSLError(1, "
            "'[SSL] record layer failure (_ssl.c:1032)')))"
        )

        message = describe_unreachable(URL, exc)

        assert "not speaking TLS" in message
        assert "http://lab.example.com:8000" in message
        assert "Max retries" not in message

    def test_wording_differences_between_openssl_builds_do_not_matter(self):
        """Same mistake, different OpenSSL phrasing - same advice."""
        for reason in ("WRONG_VERSION_NUMBER", "record layer failure", "UNKNOWN_PROTOCOL"):
            message = describe_unreachable(URL, requests.exceptions.SSLError(reason))
            assert "not speaking TLS" in message

    def test_an_untrusted_certificate_is_not_mistaken_for_plain_http(self):
        exc = requests.exceptions.SSLError(
            "certificate verify failed: self-signed certificate (_ssl.c:1000)"
        )

        message = describe_unreachable(URL, exc)

        assert "certificate" in message
        assert "not speaking TLS" not in message

    def test_a_refused_connection_says_nothing_is_listening(self):
        exc = requests.exceptions.ConnectionError(
            "Max retries exceeded ... (Caused by NewConnectionError('...: "
            "Failed to establish a new connection: [Errno 111] Connection refused'))"
        )

        assert "nothing is listening" in describe_unreachable(URL, exc)

    def test_an_unresolvable_host_says_so(self):
        exc = requests.exceptions.ConnectionError(
            "Caused by NameResolutionError(\"Failed to resolve 'lab.example.com'\")"
        )

        assert "does not resolve" in describe_unreachable(URL, exc)

    def test_a_timeout_says_so(self):
        assert "in time" in describe_unreachable(URL, requests.exceptions.ConnectTimeout("slow"))

    def test_anything_else_keeps_the_original_text(self):
        exc = requests.exceptions.RequestException("something else entirely")

        assert "something else entirely" in describe_unreachable(URL, exc)


class TestSignInReportsTheRealProblem:
    def test_a_closed_port_does_not_look_like_a_bad_token(self, unused_port):
        """The failure users hit first: right credentials, wrong address."""
        client = LabNexusClient(f"http://127.0.0.1:{unused_port}", timeout=2)

        with pytest.raises(AuthError) as caught:
            client.authenticate_pyprobe("me@lab.org", PYPROBE_TOKEN)

        message = str(caught.value)
        assert "nothing is listening" in message
        assert "rejected" not in message  # never blame the credentials for this


@pytest.fixture
def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
