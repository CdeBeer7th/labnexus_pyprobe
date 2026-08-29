"""The GUI's sign-in step: credentials in, a session and workspaces out.

Only the network half is exercised here - ``gui.sign_in`` is deliberately a
plain function so it can be tested without opening a window, which no CI box
has a display for.
"""

from __future__ import annotations

import pytest
from conftest import PYPROBE_TOKEN, PYPROBE_WORKSPACE_ID, PYPROBE_WORKSPACE_NAME, TOKEN

pytest.importorskip("tkinter", reason="this Python was built without Tk")

from labnexus_pyprobe.client import AuthError, LabNexusClient, UploadError  # noqa: E402
from labnexus_pyprobe.config import normalise_server  # noqa: E402
from labnexus_pyprobe.gui import sign_in  # noqa: E402


def url(server: str) -> str:
    return normalise_server(server, "http", allow_http=True)


class TestSignIn:
    def test_pyprobe_token_opens_a_session(self, server):
        session = sign_in(url(server), "me@lab.org", pyprobe_token=PYPROBE_TOKEN)

        assert session.email == "me@lab.org"
        assert session.client.logged_in
        assert session.client.token == TOKEN
        session.client.close()

    def test_password_opens_a_session(self, server):
        session = sign_in(url(server), "me@lab.org", password="hunter2")

        assert session.email == "me@lab.org"
        assert session.client.logged_in
        session.client.close()

    def test_fetches_the_workspaces(self, server):
        """The dropdown is populated by signing in, not by starting a sync."""
        session = sign_in(url(server), "me@lab.org", pyprobe_token=PYPROBE_TOKEN)

        assert [w.name for w in session.workspaces] == ["Kinetics", "Shared"]
        assert session.workspace_error is None
        session.client.close()

    def test_remembers_the_default_workspace(self, server):
        session = sign_in(url(server), "me@lab.org", pyprobe_token=PYPROBE_TOKEN)

        assert session.client.default_workspace_name == PYPROBE_WORKSPACE_NAME
        assert session.client.default_workspace_id == PYPROBE_WORKSPACE_ID
        session.client.close()

    def test_token_wins_over_a_password(self, server):
        """The token skips the CAPTCHA a password login may drag in."""
        session = sign_in(
            url(server), "me@lab.org", pyprobe_token=PYPROBE_TOKEN, password="wrong"
        )

        assert session.client.logged_in
        session.client.close()

    def test_bad_credentials_raise(self, server):
        with pytest.raises(AuthError):
            sign_in(url(server), "me@lab.org", password="wrong")

    def test_bad_token_raises(self, server):
        with pytest.raises(AuthError, match="rejected"):
            sign_in(url(server), "me@lab.org", pyprobe_token="lnxp_wrong")

    def test_survives_an_unreadable_workspace_list(self, server, monkeypatch):
        """A session that cannot list workspaces can still upload to a typed id."""

        def boom(self):
            raise UploadError("Could not list workspaces: HTTP 500")

        monkeypatch.setattr(LabNexusClient, "workspaces", boom)

        session = sign_in(url(server), "me@lab.org", pyprobe_token=PYPROBE_TOKEN)

        assert session.client.logged_in
        assert session.workspaces == []
        assert "HTTP 500" in session.workspace_error
        session.client.close()
