"""Each user's own model keys (Claude, GPT, Gemini, Groq), kept encrypted in the console database.

A key is shown once, when it is typed; afterwards only a masked hint is returned. One key (with its
chosen model) can be the active one: that user's questions then run on it and do not use the shared
daily budget. With no active key, questions run on the console's own model as before.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from app import crypto
from app.agents.providers import PROVIDERS
from app.db import connect

SCHEMA = """
create table if not exists model_keys (
    owner text not null, provider text not null, secret text not null, hint text not null,
    model text not null, models text not null default '[]', active integer not null default 0,
    created_at real not null, updated_at real not null,
    primary key (owner, provider));
"""


class ModelKeyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.key_file = path.parent / ".console_key"
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self):
        return connect(self.path)

    @staticmethod
    def _public(row: sqlite3.Row) -> dict:
        spec = PROVIDERS[row["provider"]]
        return {"provider": row["provider"], "label": spec.label, "hint": row["hint"], "model": row["model"],
                "models": json.loads(row["models"]), "active": bool(row["active"]),
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def list(self, owner: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute("select * from model_keys where owner = ? order by created_at", (owner,)).fetchall()
        return [self._public(row) for row in rows]

    def save(self, owner: str, provider: str, key: str, model: str, models: list[str]) -> dict:
        """Add or replace this provider's key; it becomes the active one."""
        now = time.time()
        with self._db() as db:
            db.execute("update model_keys set active = 0 where owner = ?", (owner,))
            db.execute(
                "insert into model_keys (owner, provider, secret, hint, model, models, active, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?, 1, ?, ?) on conflict (owner, provider) do update set "
                "secret = excluded.secret, hint = excluded.hint, model = excluded.model, models = excluded.models, "
                "active = 1, updated_at = excluded.updated_at",
                (owner, provider, crypto.seal(key, self.key_file), crypto.mask(key), model, json.dumps(models),
                 now, now))
            row = db.execute("select * from model_keys where owner = ? and provider = ?", (owner, provider)).fetchone()
        return self._public(row)

    def choose(self, owner: str, provider: str | None, model: str | None = None) -> None:
        """Make this key (and model) the active one; None goes back to the console's own model."""
        with self._db() as db:
            if provider is not None:
                row = db.execute("select models from model_keys where owner = ? and provider = ?",
                                 (owner, provider)).fetchone()
                if not row:
                    raise KeyError(provider)
                if model and json.loads(row["models"]) and model not in json.loads(row["models"]):
                    raise ValueError(f"This key cannot use {model}.")
            db.execute("update model_keys set active = 0 where owner = ?", (owner,))
            if provider is not None:
                db.execute("update model_keys set active = 1, updated_at = ?"
                           + (", model = ?" if model else "") + " where owner = ? and provider = ?",
                           (time.time(), *([model] if model else []), owner, provider))

    def remove(self, owner: str, provider: str) -> bool:
        with self._db() as db:
            return db.execute("delete from model_keys where owner = ? and provider = ?",
                              (owner, provider)).rowcount > 0

    def active(self, owner: str) -> dict | None:
        """The active key, decrypted, for running a question: {provider, model, key, version}."""
        with self._db() as db:
            row = db.execute("select * from model_keys where owner = ? and active = 1", (owner,)).fetchone()
        if not row:
            return None
        return {"provider": row["provider"], "model": row["model"], "version": row["updated_at"],
                "key": crypto.unseal(row["secret"], self.key_file)}
