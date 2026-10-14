"""Who may use the console, and how much: accounts, sessions, a daily token budget, limits, an audit log.

The console is public, and every live question spends tokens from one Groq account (200K a day on
the free tier). So every API call except /api/health needs a signed-in user, live questions stop
when the day's budget is spent, and each user may ask only so many questions an hour.

Everything is kept in one SQLite file (CONSOLE_DB, default checkpoints/console.sqlite, which is a
volume on the server so it survives deploys). Accounts are created by the operator from the
command line (python -m app.api.users add <name>); there is no sign-up page.

Stored safely: passwords as scrypt hashes with a random salt, sessions as SHA-256 hashes of the
cookie value, so a copy of the database file signs nobody in.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "checkpoints" / "console.sqlite"
SESSION_COOKIE = "analyst_session"
SESSION_HOURS = 12
FAILED_LOGINS_ALLOWED = 5  # per user name, within FAILED_LOGIN_WINDOW_S; then wait
FAILED_LOGIN_WINDOW_S = 15 * 60
SCRYPT = {"n": 2**14, "r": 8, "p": 1}

SCHEMA = """
create table if not exists users (
    name text primary key, password_hash text not null, created_at real not null);
create table if not exists sessions (
    token_hash text primary key, user_name text not null, expires_at real not null);
create table if not exists failed_logins (user_name text not null, at real not null);
create table if not exists questions (
    id integer primary key autoincrement, user_name text not null, question text not null,
    asked_at real not null, day text not null, status text not null default 'running',
    tokens integer not null default 0, trace_id text, thread_id text);
create index if not exists questions_by_day on questions (day);
create index if not exists questions_by_user on questions (user_name, asked_at);
"""


class LimitReached(Exception):
    """The question was refused before any tokens were spent; the message says why and until when."""


@dataclass(frozen=True)
class Limits:
    daily_tokens: int = 150_000      # leaves room under Groq's 200K a day for evals and testing
    questions_per_hour: int = 30     # per user

    @classmethod
    def from_env(cls) -> Limits:
        return cls(daily_tokens=int(os.environ.get("DAILY_TOKEN_BUDGET", cls.daily_tokens)),
                   questions_per_hour=int(os.environ.get("QUESTIONS_PER_HOUR", cls.questions_per_hour)))


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, _digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), stored)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")  # Groq's daily limit resets on UTC days


class SecurityStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or os.environ.get("CONSOLE_DB") or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """One short connection per operation: safe from any thread, and nothing stays locked."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:  # commits on success, rolls back on error
                yield db
        finally:
            db.close()

    # ---- accounts ------------------------------------------------------------------------

    def add_user(self, name: str, password: str) -> None:
        if len(password) < 10:
            raise ValueError("Use a password of at least 10 characters.")
        with self._db() as db:
            db.execute("insert or replace into users values (?, ?, ?)", (name, hash_password(password), time.time()))
            db.execute("delete from sessions where user_name = ?", (name,))  # a new password signs out everywhere

    def remove_user(self, name: str) -> bool:
        with self._db() as db:
            db.execute("delete from sessions where user_name = ?", (name,))
            return db.execute("delete from users where name = ?", (name,)).rowcount > 0

    def users(self) -> list[str]:
        with self._db() as db:
            return [row["name"] for row in db.execute("select name from users order by name")]

    # ---- sessions ------------------------------------------------------------------------

    def sign_in(self, name: str, password: str) -> str | None:
        """A new session token, or None. Repeated failures lock the name for a while."""
        now = time.time()
        with self._db() as db:
            recent = db.execute("select count(*) from failed_logins where user_name = ? and at > ?",
                                (name, now - FAILED_LOGIN_WINDOW_S)).fetchone()[0]
            if recent >= FAILED_LOGINS_ALLOWED:
                raise LimitReached("Too many failed sign-ins. Try again in 15 minutes.")
            row = db.execute("select password_hash from users where name = ?", (name,)).fetchone()
            # Hash even for an unknown name, so the response time does not reveal which names exist.
            valid = check_password(password, row["password_hash"] if row else hash_password("x"))
            if not (row and valid):
                db.execute("insert into failed_logins values (?, ?)", (name, now))
                return None
            db.execute("delete from failed_logins where user_name = ?", (name,))
            db.execute("delete from sessions where expires_at < ?", (now,))
            token = secrets.token_urlsafe(32)
            db.execute("insert into sessions values (?, ?, ?)", (_token_hash(token), name, now + SESSION_HOURS * 3600))
            return token

    def user_for(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._db() as db:
            row = db.execute("select user_name from sessions where token_hash = ? and expires_at > ?",
                             (_token_hash(token), time.time())).fetchone()
            return row["user_name"] if row else None

    def sign_out(self, token: str | None) -> None:
        if token:
            with self._db() as db:
                db.execute("delete from sessions where token_hash = ?", (_token_hash(token),))

    # ---- budget, limits and the audit log ------------------------------------------------

    def tokens_today(self) -> int:
        with self._db() as db:
            return db.execute("select coalesce(sum(tokens), 0) from questions where day = ?", (today(),)).fetchone()[0]

    def check_budget(self, limits: Limits) -> None:
        if self.tokens_today() >= limits.daily_tokens:
            raise LimitReached(f"Today's budget of {limits.daily_tokens:,} model tokens is used up. "
                               "Live questions start again at midnight UTC; saved runs still replay.")

    def start_question(self, user: str, question: str, limits: Limits, thread_id: str) -> int:
        """Check the limits, then log the question. Raises LimitReached before anything is spent."""
        now = time.time()
        self.check_budget(limits)
        with self._db() as db:
            asked = db.execute("select count(*) from questions where user_name = ? and asked_at > ?",
                               (user, now - 3600)).fetchone()[0]
            if asked >= limits.questions_per_hour:
                raise LimitReached(f"You have asked {asked} questions in the last hour, the limit is "
                                   f"{limits.questions_per_hour}. Try again a little later.")
            return db.execute("insert into questions (user_name, question, asked_at, day, thread_id) "
                              "values (?, ?, ?, ?, ?)", (user, question, now, today(), thread_id)).lastrowid

    def question_for_thread(self, thread_id: str) -> int | None:
        with self._db() as db:
            row = db.execute("select id from questions where thread_id = ? order by id desc limit 1",
                             (thread_id,)).fetchone()
            return row["id"] if row else None

    def finish_question(self, question_id: int, status: str, tokens: int = 0, trace_id: str | None = None) -> None:
        with self._db() as db:
            db.execute("update questions set status = ?, tokens = ?, trace_id = coalesce(?, trace_id) where id = ?",
                       (status, tokens, trace_id, question_id))

    def usage(self, user: str, limits: Limits) -> dict:
        with self._db() as db:
            asked = db.execute("select count(*) from questions where user_name = ? and asked_at > ?",
                               (user, time.time() - 3600)).fetchone()[0]
        used = self.tokens_today()
        return {"tokens_today": used, "daily_tokens": limits.daily_tokens,
                "tokens_left": max(limits.daily_tokens - used, 0),
                "questions_last_hour": asked, "questions_per_hour": limits.questions_per_hour}

    def history(self, user: str | None = None, limit: int = 50) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "select user_name, question, asked_at, status, tokens, trace_id from questions "
                + ("where user_name = ? " if user else "") + "order by asked_at desc limit ?",
                (user, limit) if user else (limit,))
            return [dict(row) for row in rows]
