"""Solve a Cap.js (https://trycap.dev) proof-of-work CAPTCHA challenge.

LabNexus gates login and re-verification behind Cap.js instead of an
interactive CAPTCHA, specifically because its default challenge format is a
plain SHA-256 proof-of-work puzzle with no human interaction required - a
headless client is meant to be able to solve it. The salts and targets are
not sent over the wire; both sides derive them from the challenge token with
the same FNV-1a hash and xorshift PRNG, so this is a faithful port of
capjs-core's ``generateChallenge``/``validateChallenge`` (format 1) rather
than a guess at the protocol.
"""

from __future__ import annotations

import hashlib

_MASK32 = 0xFFFFFFFF
_FNV_OFFSET_BASIS = 2166136261


def _fnv1a(text: str, state: int = _FNV_OFFSET_BASIS) -> int:
    """32-bit FNV-1a, extended to resume from a prior state (``fnv1aResume``)."""
    h = state & _MASK32
    for ch in text:
        h ^= ord(ch)
        h &= _MASK32
        h = (
            h
            + ((h << 1) & _MASK32)
            + ((h << 4) & _MASK32)
            + ((h << 7) & _MASK32)
            + ((h << 8) & _MASK32)
            + ((h << 24) & _MASK32)
        ) & _MASK32
    return h


def _prng_from_hash(seed: int, length: int) -> str:
    """Deterministic hex string of *length* chars, seeded from *seed* (xorshift32)."""
    state = seed & _MASK32
    chunks: list[str] = []
    produced = 0
    while produced < length:
        state = (state ^ ((state << 13) & _MASK32)) & _MASK32
        state = (state ^ (state >> 17)) & _MASK32
        state = (state ^ ((state << 5) & _MASK32)) & _MASK32
        chunks.append(f"{state:08x}")
        produced += 8
    return "".join(chunks)[:length]


def _solve_one(salt: str, target: str) -> int:
    """Smallest nonce such that sha256(salt + nonce) starts with *target* (hex)."""
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{salt}{nonce}".encode()).hexdigest()
        if digest.startswith(target):
            return nonce
        nonce += 1


def solve_pow_challenge(token: str, count: int, size: int, difficulty: int) -> list[int]:
    """Solve a Cap.js format-1 (sha256-pow) challenge.

    *token* is the challenge JWT returned alongside ``{c, s, d}`` from
    ``POST /cap/{site_key}/challenge``; *count*/*size*/*difficulty* are that
    same ``c``/``s``/``d``. Returns one nonce per sub-challenge, in order,
    ready to submit as ``solutions`` to ``POST /cap/{site_key}/redeem``.
    """
    token_fnv = _fnv1a(token)
    solutions = []
    for i in range(1, count + 1):
        salt_seed = _fnv1a(str(i), state=token_fnv)
        target_seed = _fnv1a("d", state=salt_seed)
        salt = _prng_from_hash(salt_seed, size)
        target = _prng_from_hash(target_seed, difficulty)
        solutions.append(_solve_one(salt, target))
    return solutions
