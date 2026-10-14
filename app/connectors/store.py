"""A user's connections to outside tools: what they are, and their credentials, encrypted.

A credential (token, webhook address, refresh token) is shown to its owner only while typing it in;
once saved, the console only ever shows a masked hint such as "secr…ab". Each connection is private
to its owner.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app import crypto

SCHEMA = """
create table if not exists connections (
    id text primary key, owner text not null, kind text not null, name text not null,
    settings text not null default '{}', secret text, hint text, created_at real not null, last_used_at real);
create index if not exists connections_by_owner on connections (owner);
create table if not exists oauth_states (
    state text primary key, owner text not null, kind text not null, verifier text not null, name text not null,
    created_at real not null);
"""


class ConnectionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.key_file = path.parent / ".console_key"
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, owner: str, kind: str, name: str, settings: dict, secret: dict | None) -> dict:
        connection_id = secrets.token_hex(6)
        sealed = crypto.seal(json.dumps(secret), self.key_file) if secret else None
        hint = ", ".join(f"{k}: {crypto.mask(str(v))}" for k, v in (secret or {}).items() if v)
        with self._db() as db:
            db.execute("insert into connections (id, owner, kind, name, settings, secret, hint, created_at) "
                       "values (?, ?, ?, ?, ?, ?, ?, ?)",
                       (connection_id, owner, kind, name.strip()[:80] or kind, json.dumps(settings), sealed, hint,
                        time.time()))
        return self.get(owner, connection_id)

    @staticmethod
    def _public(row: sqlite3.Row) -> dict:
        return {"id": row["id"], "kind": row["kind"], "name": row["name"], "settings": json.loads(row["settings"]),
                "hint": row["hint"], "created_at": row["created_at"], "last_used_at": row["last_used_at"]}

    def list(self, owner: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute("select * from connections where owner = ? order by created_at desc", (owner,))
            return [self._public(row) for row in rows]

    def get(self, owner: str, connection_id: str) -> dict:
        with self._db() as db:
            row = db.execute("select * from connections where id = ? and owner = ?", (connection_id, owner)).fetchone()
        if not row:
            raise KeyError(connection_id)
        return self._public(row)

    def secret(self, owner: str, connection_id: str) -> dict:
        """The decrypted credential, for the code that calls the service; never sent to a browser."""
        with self._db() as db:
            row = db.execute("select secret from connections where id = ? and owner = ?", (connection_id, owner)).fetchone()
            if not row:
                raise KeyError(connection_id)
            db.execute("update connections set last_used_at = ? where id = ?", (time.time(), connection_id))
        return json.loads(crypto.unseal(row["secret"], self.key_file)) if row["secret"] else {}

    def update_secret(self, owner: str, connection_id: str, secret: dict) -> None:
        """For services whose access token is renewed (Google): the new one replaces the old, still encrypted."""
        with self._db() as db:
            db.execute("update connections set secret = ? where id = ? and owner = ?",
                       (crypto.seal(json.dumps(secret), self.key_file), connection_id, owner))

    def remove(self, owner: str, connection_id: str) -> None:
        self.get(owner, connection_id)
        with self._db() as db:
            db.execute("delete from connections where id = ?", (connection_id,))

    # ---- sign-in with another service (OAuth) -----------------------------------------------

    def new_state(self, owner: str, kind: str, name: str) -> tuple[str, str]:
        """A one-use state (against forged callbacks) and a PKCE verifier for the authorization request."""
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        with self._db() as db:
            db.execute("delete from oauth_states where created_at < ?", (time.time() - 600,))
            db.execute("insert into oauth_states values (?, ?, ?, ?, ?, ?)", (state, owner, kind, verifier, name, time.time()))
        return state, verifier

    def take_state(self, state: str) -> dict | None:
        """The waiting sign-in this state belongs to (if under 10 minutes old); it cannot be used twice."""
        with self._db() as db:
            row = db.execute("select * from oauth_states where state = ? and created_at > ?",
                             (state, time.time() - 600)).fetchone()
            db.execute("delete from oauth_states where state = ?", (state,))
        return dict(row) if row else None
