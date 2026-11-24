"""
Authenticated encryption — one primitive, no keys of its own.

This module exists because of an ordering problem, and the ordering problem is
worth stating because it is the whole reason a "small" refactor was made.

The keyring in `security.py` used to load itself at import time. An envelope
(`app/kms.py`, docs/30) wraps the *root* key with a key-encryption key, which
means unwrapping has to happen before the keyring can exist — and unwrapping is
itself an authenticated-encryption operation, which lived inside the module that
was still importing. A deferred import inside a function would have returned a
half-initialised module; a lazy `KEYS` proxy would have moved a fail-closed boot
check from boot to the first request. Neither is worth the cleverness.

So the primitive lives here: ChaCha20 (RFC 8439) with encrypt-then-MAC
(HMAC-SHA256, truncated to 128 bits), an explicit 32-byte key, and an explicit
associated-data string. It knows nothing about tenants, keyrings, config or the
envelope. `security.seal`/`unseal` are thin wrappers over it that supply the
active `field_enc` subkey, so every existing call site is unchanged.

Not a general-purpose crypto library. It is the two functions this application
needs, with the one declaration that matters: **a tag mismatch is a
`ValueError("INTEGRITY_FAILURE")`, and callers must treat it as a failure, not a
fallback.** An application that "tries the old key on error" turns an
authentication failure into a decryption oracle.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct

NONCE_BYTES = 12
TAG_BYTES = 16
KEY_BYTES = 32


def _rotl32(v: int, c: int) -> int:
    return ((v << c) & 0xFFFFFFFF) | (v >> (32 - c))


def _qr(s: list[int], a: int, b: int, c: int, d: int) -> None:
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF
    s[d] = _rotl32(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF
    s[b] = _rotl32(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF
    s[d] = _rotl32(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF
    s[b] = _rotl32(s[b] ^ s[c], 7)


def chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    const = b"expand 32-byte k"
    st = (list(struct.unpack("<4I", const)) + list(struct.unpack("<8I", key))
          + [counter & 0xFFFFFFFF, 0, 0, 0] + list(struct.unpack("<3I", nonce)))
    w = st[:]
    for _ in range(10):
        _qr(w, 0, 4, 8, 12)
        _qr(w, 1, 5, 9, 13)
        _qr(w, 2, 6, 10, 14)
        _qr(w, 3, 7, 11, 15)
        _qr(w, 0, 5, 10, 15)
        _qr(w, 1, 6, 11, 12)
        _qr(w, 2, 7, 8, 13)
        _qr(w, 3, 4, 9, 14)
    return struct.pack("<16I", *[(w[i] + st[i]) & 0xFFFFFFFF for i in range(16)])


def keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    ctr = 0
    while len(out) < length:
        out += chacha20_block(key, ctr, nonce)
        ctr += 1
    return bytes(out[:length])


def _check_key(key: bytes) -> bytes:
    if not isinstance(key, (bytes, bytearray)) or len(key) != KEY_BYTES:
        raise ValueError(f"a key must be {KEY_BYTES} bytes")
    return bytes(key)


def seal_bytes(plain: bytes, key: bytes, aad: str = "") -> str:
    """Encrypt-then-MAC. Returns a URL-safe opaque string with no padding."""
    k = _check_key(key)
    nonce = secrets.token_bytes(NONCE_BYTES)
    ks = keystream(k, nonce, len(plain))
    ct = bytes(a ^ b for a, b in zip(plain, ks))
    tag = hmac.new(k, nonce + ct + aad.encode(), hashlib.sha256).digest()[:TAG_BYTES]
    return base64.urlsafe_b64encode(nonce + ct + tag).decode().rstrip("=")


def open_bytes(blob: str, key: bytes, aad: str = "") -> bytes:
    k = _check_key(key)
    pad = "=" * (-len(blob) % 4)
    raw = base64.urlsafe_b64decode(blob + pad)
    if len(raw) < NONCE_BYTES + TAG_BYTES:
        raise ValueError("INTEGRITY_FAILURE")
    nonce, ct, tag = raw[:NONCE_BYTES], raw[NONCE_BYTES:-TAG_BYTES], raw[-TAG_BYTES:]
    expect = hmac.new(k, nonce + ct + aad.encode(), hashlib.sha256).digest()[:TAG_BYTES]
    if not hmac.compare_digest(tag, expect):
        raise ValueError("INTEGRITY_FAILURE")
    ks = keystream(k, nonce, len(ct))
    return bytes(a ^ b for a, b in zip(ct, ks))


def seal(plaintext: str, key: bytes, aad: str = "") -> str:
    if plaintext is None:
        return ""
    return seal_bytes(plaintext.encode("utf-8"), key, aad)


def unseal(blob: str, key: bytes, aad: str = "") -> str:
    if not blob:
        return ""
    return open_bytes(blob, key, aad).decode("utf-8")
