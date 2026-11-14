"""RFC 6238 TOTP (SHA-1, 30 s, 6 digits) for MFA-protected TEST accounts whose seed the operator
provides — so a session can be established without a human typing codes."""

from __future__ import annotations

import hashlib
import hmac
import struct
import time

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def base32_decode(secret: str) -> bytes:
    """Decode a base32 string (padding, spaces and casing ignored)."""
    clean = "".join(ch for ch in secret.upper() if ch not in " =-\t\r\n")
    bits = ""
    for ch in clean:
        v = _ALPHABET.find(ch)
        if v < 0:
            raise ValueError("totp_secret is not base32")
        bits += format(v, "05b")
    return bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits) - 7, 8))


def totp(secret: str, now: float | None = None, step: int = 30, digits: int = 6) -> str:
    """The TOTP code for ``secret`` at time ``now`` (seconds; the wall clock when omitted)."""
    if now is None:
        now = time.time()
    counter = int(now // step)
    digest = hmac.new(base32_decode(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 10**digits
    return str(code).zfill(digits)
