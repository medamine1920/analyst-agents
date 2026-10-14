"""Encryption for secrets the console must be able to read back: connector tokens, two-step sign-in keys.

Passwords and API keys are hashed (they only need checking); these are encrypted, because the console
has to use them. The key comes from CONSOLE_SECRET (any passphrase, stretched into a key) or, if that
is not set, a key file created on first use next to the console database, readable by the console only.
"""

from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_KEY_FILE = Path(__file__).resolve().parents[1] / "checkpoints" / ".console_key"


@lru_cache(maxsize=4)
def box(key_file: Path | None = None) -> Fernet:
    passphrase = os.environ.get("CONSOLE_SECRET")
    if passphrase:
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(passphrase.encode("utf-8")).digest()))
    path = Path(key_file or os.environ.get("CONSOLE_KEY_FILE") or DEFAULT_KEY_FILE)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Fernet.generate_key())
        path.chmod(0o600)
    return Fernet(path.read_bytes().strip())


def seal(text: str, key_file: Path | None = None) -> str:
    return box(key_file).encrypt(text.encode("utf-8")).decode("ascii")


def unseal(sealed: str, key_file: Path | None = None) -> str:
    try:
        return box(key_file).decrypt(sealed.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:  # the key changed: the secret cannot be recovered, only replaced
        raise ValueError("A stored secret could not be decrypted; enter it again.") from exc


def mask(secret: str) -> str:
    """What a stored secret looks like on screen once saved: never the secret itself."""
    return f"{secret[:4]}…{secret[-2:]}" if len(secret) > 10 else "••••••"
