"""HTTP client for the LabNexus server."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from labnexus_plate_parsers import SpectrometerModel, UnifiedPlateReaderOutput

from .captcha import solve_pow_challenge

#: The server mounts its REST and spectrometer routes under /api, but auth sits
#: at the root, so the two cannot share one base.
API_PREFIX = "/api"

#: Where a pyProbe access token is exchanged for a bearer session. Deliberately
#: outside API_PREFIX: it is what a bench client calls before it has a session.
PYPROBE_AUTH_PATH = "/auth/pyprobe/token"


class AuthError(RuntimeError):
    """Raised when the server rejects the supplied credentials or token."""


class CaptchaRequired(AuthError):
    """The server wants a CAPTCHA solved in a browser before it will accept more.

    Raised separately from a plain auth failure because the fix is different:
    the credentials are fine, the user just has to sign in to the web UI once.
    """


class UploadError(RuntimeError):
    """Raised when a file could not be handed to the server."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Workspace:
    """A workspace the signed-in user can upload into."""

    id: str
    name: str
    owned: bool = False

    def __str__(self) -> str:
        return self.name


class LabNexusClient:
    """Thin wrapper around the handful of LabNexus endpoints pyProbe uses."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 60.0,
        retries: int = 2,
        verify_tls: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, retries)
        self.session = requests.Session()
        self.session.verify = verify_tls
        self.token: str | None = None
        #: Filled in by :meth:`authenticate_pyprobe` - the workspace the server
        #: files this account's unattributed runs under. Lets the bench client
        #: report where uploads went without a separate lookup.
        self.default_workspace_id: str | None = None
        self.default_workspace_name: str | None = None

    def _api(self, path: str) -> str:
        return f"{self.base_url}{API_PREFIX}/{path.lstrip('/')}"

    # -- session ---------------------------------------------------------

    @property
    def logged_in(self) -> bool:
        return self.token is not None

    def login(self, email: str, password: str) -> str:
        """Exchange credentials for a JWT and remember it for later uploads."""
        data = {"username": email, "password": password}
        cap_token = self.solve_captcha()
        if cap_token:
            data["cap_token"] = cap_token

        try:
            response = self.session.post(
                f"{self.base_url}/auth/jwt/login",
                data=data,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AuthError(f"Could not reach {self.base_url}: {exc}") from exc

        if response.status_code in (400, 401, 403):
            raise AuthError("The server rejected that email/password combination.")
        if not response.ok:
            raise AuthError(f"Login failed: HTTP {response.status_code} - {response.text[:200]}")

        try:
            token = response.json()["access_token"]
        except (ValueError, KeyError) as exc:
            raise AuthError("Login succeeded but the server returned no access token.") from exc

        self.use_token(token)
        return token

    def authenticate_pyprobe(self, email: str, access_token: str) -> str:
        """Exchange a pyProbe access token for a session, and remember it.

        This is the unattended path: no password, no CAPTCHA, no second factor
        to type. The user mints the token in the web UI under Settings →
        pyProbe, and it is presented together with the account email — the
        token alone is not enough. The session it buys expires with the token,
        whichever comes first, so a long-lived bench install re-authenticates
        on each run rather than holding one session open forever.

        Returns the account email the server confirmed the token belongs to.
        """
        try:
            response = self.session.post(
                f"{self.base_url}{PYPROBE_AUTH_PATH}",
                json={"email": email, "token": access_token},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AuthError(f"Could not reach {self.base_url}: {exc}") from exc

        if response.status_code == 429:
            raise AuthError(
                "Too many sign-in attempts from this machine. Wait a few minutes and try again."
            )
        if response.status_code in (400, 401, 403):
            raise AuthError(
                "The server rejected that email and pyProbe token. Check the "
                "token has not expired or been revoked (Settings -> pyProbe in "
                "the web interface)."
            )
        if not response.ok:
            raise AuthError(
                f"pyProbe sign-in failed: HTTP {response.status_code} - {response.text[:200]}"
            )

        try:
            payload = response.json()
            token = payload["access_token"]
        except (ValueError, KeyError) as exc:
            raise AuthError(
                "pyProbe sign-in succeeded but the server returned no session token."
            ) from exc

        self.use_token(token)
        self.default_workspace_id = payload.get("default_workspace_id") or None
        self.default_workspace_name = payload.get("default_workspace_name") or None
        return str(payload.get("email") or email)

    def use_token(self, token: str) -> None:
        """Authenticate with a token obtained elsewhere (e.g. ``--token``)."""
        self.token = token
        self.session.headers["Authorization"] = f"Bearer {token}"

    # -- CAPTCHA -----------------------------------------------------------

    def _captcha_config(self) -> dict | None:
        """The server's public CAPTCHA config, or None if it has none enabled."""
        try:
            response = self.session.get(f"{self.base_url}/captcha/config", timeout=self.timeout)
        except requests.RequestException:
            return None
        if not response.ok:
            return None
        try:
            config = response.json()
        except ValueError:
            return None
        if not config.get("enabled") or not config.get("site_key"):
            return None
        return config

    def solve_captcha(self) -> str | None:
        """Solve a Cap.js proof-of-work challenge and redeem it for a cap_token.

        Returns None when the server has no CAPTCHA configured - the common
        case, and the only one that must not raise, since callers use this
        before every login. Raises CaptchaRequired if the server demands a
        challenge this client cannot solve headlessly (a time-lock puzzle, or
        browser instrumentation) - those need a real browser, so the user has
        to sign in to the web UI once instead.
        """
        config = self._captcha_config()
        if config is None:
            return None

        cap_url = f"{self.base_url}/cap/{config['site_key']}"
        try:
            response = self.session.post(f"{cap_url}/challenge", json={}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise CaptchaRequired(f"Could not reach the CAPTCHA server: {exc}") from exc
        if not response.ok:
            raise CaptchaRequired(
                f"The CAPTCHA server rejected the challenge request (HTTP {response.status_code})."
            )
        try:
            challenge = response.json()
        except ValueError as exc:
            raise CaptchaRequired("The CAPTCHA server returned an unreadable challenge.") from exc

        # format 2 (time-lock puzzles) and instrumentation both need a real
        # browser to solve - this client only speaks plain proof-of-work.
        if challenge.get("format") == 2 or "challenge" not in challenge:
            raise CaptchaRequired(
                "This server's CAPTCHA needs a real browser to solve. Sign in "
                "to the LabNexus web interface once, then restart pyProbe."
            )

        params = challenge["challenge"]
        solutions = solve_pow_challenge(challenge["token"], params["c"], params["s"], params["d"])

        try:
            redeemed = self.session.post(
                f"{cap_url}/redeem",
                json={"token": challenge["token"], "solutions": solutions},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CaptchaRequired(f"Could not redeem the CAPTCHA challenge: {exc}") from exc
        if not redeemed.ok:
            raise CaptchaRequired(
                f"The CAPTCHA server rejected the solved challenge (HTTP {redeemed.status_code})."
            )
        try:
            result = redeemed.json()
        except ValueError as exc:
            raise CaptchaRequired(
                "The CAPTCHA server returned an unreadable redemption result."
            ) from exc

        if not result.get("success") or not result.get("token"):
            raise CaptchaRequired("The CAPTCHA server did not accept the solved challenge.")
        return result["token"]

    def reverify(self) -> None:
        """Solve a fresh CAPTCHA and clear a pending re-verification flag.

        Called after ``_raise_for_auth`` raises CaptchaRequired mid-session
        (an IP region change or rate limit tripped the server's re-check) -
        on success, the request that triggered it can simply be retried.
        """
        if not self.logged_in:
            raise AuthError("No active session - log in before re-verifying.")
        cap_token = self.solve_captcha()
        if cap_token is None:
            raise CaptchaRequired(
                "The server wants CAPTCHA re-verification but has none configured."
            )
        try:
            response = self.session.post(
                f"{self.base_url}/captcha/reverify",
                json={"cap_token": cap_token},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CaptchaRequired(f"Could not reach the CAPTCHA server: {exc}") from exc
        if not response.ok:
            raise CaptchaRequired(f"CAPTCHA re-verification failed (HTTP {response.status_code}).")

    def ping(self) -> bool:
        """Best-effort reachability check; never raises."""
        try:
            self.session.get(f"{self.base_url}/", timeout=min(self.timeout, 10))
        except requests.RequestException:
            return False
        return True

    def _raise_for_auth(self, response: requests.Response) -> None:
        """Turn the server's auth-ish responses into the right exception."""
        if response.status_code not in (401, 403):
            return
        if "CAPTCHA_REVERIFICATION_REQUIRED" in response.text:
            raise CaptchaRequired(
                "The server needs a CAPTCHA re-verified before it will accept more uploads."
            )
        raise AuthError("Session expired or not permitted to upload.")

    # -- lookups ---------------------------------------------------------

    def workspaces(self) -> list[Workspace]:
        """The workspaces this user can upload into, owned and shared."""
        if not self.logged_in:
            raise AuthError("No active session - log in before listing workspaces.")
        try:
            response = self.session.get(self._api("/workspaces/"), timeout=self.timeout)
        except requests.RequestException as exc:
            raise UploadError(f"Could not list workspaces: {exc}") from exc

        self._raise_for_auth(response)
        if not response.ok:
            raise UploadError(
                f"Could not list workspaces: HTTP {response.status_code}",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise UploadError("The server returned an unreadable workspace list.") from exc

        return [
            Workspace(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                owned=bool(item.get("owned", False)),
            )
            for item in payload
            if isinstance(item, dict) and item.get("id")
        ]

    def server_models(self) -> dict:
        """What the server can parse, and which parser release it is running.

        Used to warn when the bench client and the server disagree - two
        different labnexus-plate-parsers releases can produce different
        structured output from the same file.
        """
        if not self.logged_in:
            raise AuthError("No active session - log in first.")
        try:
            response = self.session.get(
                self._api("/files/spectrometer/models"), timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise UploadError(f"Could not read the server's model list: {exc}") from exc
        self._raise_for_auth(response)
        if not response.ok:
            raise UploadError(
                f"Could not read the server's model list: HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()

    # -- uploads ---------------------------------------------------------

    def _post_with_retries(
        self,
        url: str,
        *,
        build_request: Callable[[], AbstractContextManager[dict[str, Any]]],
    ) -> requests.Response:
        """POST, retrying transient failures with a short backoff.

        ``build_request`` returns the kwargs for one attempt; it is called fresh
        each time because a file handle cannot be replayed after a failed send.
        """
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with build_request() as kwargs:
                    response = self.session.post(url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                last_error = UploadError(f"Network error: {exc}")
            else:
                if response.ok:
                    return response
                self._raise_for_auth(response)
                last_error = UploadError(
                    f"HTTP {response.status_code} - {response.text[:200]}",
                    status_code=response.status_code,
                )
                # 4xx other than auth will not fix itself on a retry.
                if response.status_code < 500:
                    break

            if attempt < self.retries:
                time.sleep(2**attempt)

        raise last_error if last_error else UploadError("Upload failed for an unknown reason.")

    def upload(self, path: Path) -> requests.Response:
        """Upload one plain file, unparsed."""
        if not self.logged_in:
            raise AuthError("No active session - log in before uploading.")

        @contextmanager
        def build() -> Iterator[dict[str, Any]]:
            with path.open("rb") as handle:
                yield {"files": {"file": (path.name, handle)}}

        return self._post_with_retries(self._api("/files/upload/pyprobe"), build_request=build)

    def upload_spectrometer(
        self,
        path: Path,
        model: SpectrometerModel,
        workspace_id: str | None = None,
        structured: UnifiedPlateReaderOutput | None = None,
    ) -> dict:
        """Upload a plate-reader export, with the structured parse alongside it.

        The raw file still goes up - the server stores both - but sending the
        parsed document means the bench client's parse is what gets filed,
        rather than the server re-doing the same work on a possibly different
        parser release. The server re-validates it either way.

        ``workspace_id`` is optional. Omitting it lets the server file the run
        under the account's "Pyprobe" workspace, which it creates on first use
        - the useful default for an unattended bench where nobody is around to
        choose a destination.
        """
        if not self.logged_in:
            raise AuthError("No active session - log in before uploading.")

        @contextmanager
        def build() -> Iterator[dict[str, Any]]:
            with path.open("rb") as handle:
                data = {"model": model.value}
                if structured is not None:
                    data["structured"] = structured.model_dump_json()
                yield {"data": data, "files": {"file": (path.name, handle)}}

        query = "prober=true"
        if workspace_id:
            query = f"workspace_id={workspace_id}&{query}"
        url = self._api("/files/spectrometer/upload")
        response = self._post_with_retries(f"{url}?{query}", build_request=build)
        try:
            return response.json()
        except ValueError:
            return {}

    def close(self) -> None:
        self.session.close()
