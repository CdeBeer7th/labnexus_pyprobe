"""HTTP client for the LabNexus server."""

from __future__ import annotations

import time
from pathlib import Path

import requests


class AuthError(RuntimeError):
    """Raised when the server rejects the supplied credentials or token."""


class UploadError(RuntimeError):
    """Raised when a file could not be handed to the server."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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

    # -- session ---------------------------------------------------------

    @property
    def logged_in(self) -> bool:
        return self.token is not None

    def login(self, email: str, password: str) -> str:
        """Exchange credentials for a JWT and remember it for later uploads."""
        try:
            response = self.session.post(
                f"{self.base_url}/auth/jwt/login",
                data={"username": email, "password": password},
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

    def use_token(self, token: str) -> None:
        """Authenticate with a token obtained elsewhere (e.g. ``--token``)."""
        self.token = token
        self.session.headers["Authorization"] = f"Bearer {token}"

    def ping(self) -> bool:
        """Best-effort reachability check; never raises."""
        try:
            self.session.get(f"{self.base_url}/", timeout=min(self.timeout, 10))
        except requests.RequestException:
            return False
        return True

    # -- uploads ---------------------------------------------------------

    def upload(self, path: Path) -> requests.Response:
        """Upload one file, retrying transient failures with a short backoff."""
        if not self.logged_in:
            raise AuthError("No active session - log in before uploading.")

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with path.open("rb") as handle:
                    response = self.session.post(
                        f"{self.base_url}/files/upload/pyprobe",
                        files={"file": (path.name, handle)},
                        timeout=self.timeout,
                    )
            except requests.RequestException as exc:
                last_error = UploadError(f"Network error: {exc}")
            else:
                if response.ok:
                    return response
                if response.status_code in (401, 403):
                    raise AuthError("Session expired or not permitted to upload.")
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

    def close(self) -> None:
        self.session.close()
