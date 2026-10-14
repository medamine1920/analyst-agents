"""Two-step sign-in with an authenticator app (time-based one-time codes, RFC 6238), and recovery codes.

Works with Google Authenticator, Microsoft Authenticator, 1Password and the like: the app and the
console share a secret, and both derive the same 6-digit code from it every 30 seconds.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_S = 30
DIGITS = 6
ISSUER = "Analyst Console"


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def code_at(secret: str, at: float) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", int(at // STEP_S)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return f"{number:0{DIGITS}d}"


def check_code(secret: str, code: str, now: float | None = None) -> bool:
    """Accept the current code and its neighbours, so a phone clock 30 seconds off still works."""
    code = (code or "").replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return False
    now = time.time() if now is None else now
    return any(hmac.compare_digest(code_at(secret, now + drift * STEP_S), code) for drift in (-1, 0, 1))


def setup_uri(secret: str, account: str) -> str:
    """The address an authenticator app reads from a QR code."""
    return (f"otpauth://totp/{quote(ISSUER)}:{quote(account)}?secret={secret}&issuer={quote(ISSUER)}"
            f"&digits={DIGITS}&period={STEP_S}")


def recovery_codes(count: int = 8) -> list[str]:
    """One-use codes for when the phone is lost: shown once, stored as hashes."""
    return [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(count)]


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode("utf-8")).hexdigest()
