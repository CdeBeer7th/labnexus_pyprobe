import hashlib

from labnexus_pyprobe.captcha import _fnv1a, _prng_from_hash, solve_pow_challenge


def test_fnv1a_and_prng_match_capjs_core_reference():
    """Values captured from tiagozip/cap's actual core/src/prng.js via node."""
    token = "eyJhbGciOiJIUzI1NiJ9.some-fake-challenge-token-for-testing.abcXYZ123"
    token_fnv = _fnv1a(token)
    salt_seed = _fnv1a("1", state=token_fnv)
    target_seed = _fnv1a("d", state=salt_seed)
    assert _prng_from_hash(salt_seed, 32) == "0e7a2fcb3bee2b4a2e65952904df9269"
    assert _prng_from_hash(target_seed, 4) == "3b70"


def test_solve_pow_challenge_produces_valid_solutions():
    token = "some-challenge-token"
    c, s, d = 5, 16, 3
    solutions = solve_pow_challenge(token, c, s, d)
    assert len(solutions) == c

    token_fnv = _fnv1a(token)
    for i, nonce in enumerate(solutions, start=1):
        salt_seed = _fnv1a(str(i), state=token_fnv)
        target_seed = _fnv1a("d", state=salt_seed)
        salt = _prng_from_hash(salt_seed, s)
        target = _prng_from_hash(target_seed, d)
        digest = hashlib.sha256(f"{salt}{nonce}".encode()).hexdigest()
        assert digest.startswith(target)
