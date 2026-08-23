"""Client-side CAPTCHA wiring, exercised against a fake session (no real HTTP)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pytest

from labnexus_pyprobe.captcha import _fnv1a, _prng_from_hash
from labnexus_pyprobe.client import AuthError, CaptchaRequired, LabNexusClient

SITE_KEY = "test-site-key"
CHALLENGE_TOKEN = "chal-token-abc"
C, S, D = 3, 16, 2


def _valid_solutions() -> list[int]:
    token_fnv = _fnv1a(CHALLENGE_TOKEN)
    solutions = []
    for i in range(1, C + 1):
        salt_seed = _fnv1a(str(i), state=token_fnv)
        target_seed = _fnv1a("d", state=salt_seed)
        salt = _prng_from_hash(salt_seed, S)
        target = _prng_from_hash(target_seed, D)
        nonce = 0
        while not hashlib.sha256(f"{salt}{nonce}".encode()).hexdigest().startswith(target):
            nonce += 1
        solutions.append(nonce)
    return solutions


@dataclass
class FakeResponse:
    status_code: int
    payload: dict
    text: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def json(self):
        return self.payload


@dataclass
class FakeSession:
    """Records calls and answers exactly the CAPTCHA + login/reverify routes."""

    captcha_enabled: bool = True
    calls: list[tuple[str, str, dict]] = field(default_factory=list)
    headers: dict = field(default_factory=dict)

    def get(self, url, timeout=None):
        self.calls.append(("GET", url, {}))
        if url.endswith("/captcha/config"):
            if self.captcha_enabled:
                return FakeResponse(200, {"enabled": True, "site_key": SITE_KEY})
            return FakeResponse(200, {"enabled": False, "site_key": ""})
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, data=None, json=None, timeout=None):
        self.calls.append(("POST", url, data if data is not None else json))
        if url.endswith(f"/cap/{SITE_KEY}/challenge"):
            challenge = {"c": C, "s": S, "d": D}
            return FakeResponse(200, {"challenge": challenge, "token": CHALLENGE_TOKEN})
        if url.endswith(f"/cap/{SITE_KEY}/redeem"):
            assert json["token"] == CHALLENGE_TOKEN
            assert json["solutions"] == _valid_solutions()
            redeemed = f"{SITE_KEY}:redeemid:redeemsecret"
            return FakeResponse(200, {"success": True, "token": redeemed})
        if url.endswith("/auth/jwt/login"):
            if self.captcha_enabled:
                assert data.get("cap_token") == f"{SITE_KEY}:redeemid:redeemsecret"
            return FakeResponse(200, {"access_token": "jwt-abc"})
        if url.endswith("/captcha/reverify"):
            assert json["cap_token"] == f"{SITE_KEY}:redeemid:redeemsecret"
            return FakeResponse(200, {"status": "verified"})
        raise AssertionError(f"unexpected POST {url}")


def _client(captcha_enabled: bool = True) -> LabNexusClient:
    client = LabNexusClient("https://lab.example.com")
    client.session = FakeSession(captcha_enabled=captcha_enabled)
    return client


def test_solve_captcha_returns_none_when_disabled():
    client = _client(captcha_enabled=False)
    assert client.solve_captcha() is None


def test_solve_captcha_solves_and_redeems():
    client = _client()
    token = client.solve_captcha()
    assert token == f"{SITE_KEY}:redeemid:redeemsecret"


def test_login_includes_cap_token_when_captcha_enabled():
    client = _client()
    result = client.login("me@lab.org", "hunter2")
    assert result == "jwt-abc"
    login_call = next(c for c in client.session.calls if c[1].endswith("/auth/jwt/login"))
    assert login_call[2]["cap_token"] == f"{SITE_KEY}:redeemid:redeemsecret"


def test_login_omits_cap_token_when_captcha_disabled():
    client = _client(captcha_enabled=False)
    client.login("me@lab.org", "hunter2")
    login_call = next(c for c in client.session.calls if c[1].endswith("/auth/jwt/login"))
    assert "cap_token" not in login_call[2]


def test_reverify_requires_a_session():
    client = _client()
    with pytest.raises(AuthError):
        client.reverify()


def test_reverify_solves_and_posts_cap_token():
    client = _client()
    client.token = "jwt-abc"  # pretend we're already logged in
    client.reverify()  # must not raise
    assert any(c[1].endswith("/captcha/reverify") for c in client.session.calls)


def test_format_2_challenge_falls_back_to_captcha_required():
    class Format2Session(FakeSession):
        def post(self, url, data=None, json=None, timeout=None):
            if url.endswith(f"/cap/{SITE_KEY}/challenge"):
                return FakeResponse(200, {"format": 2, "challenges": [], "token": "x"})
            return super().post(url, data=data, json=json, timeout=timeout)

    client = LabNexusClient("https://lab.example.com")
    client.session = Format2Session()
    with pytest.raises(CaptchaRequired):
        client.solve_captcha()
