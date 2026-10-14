"""Who may use the console, and how much: accounts, sessions, a daily token budget, limits, an audit log.

The console is public, and every live question spends tokens from one Groq account (200K a day on
the free tier). So every API call except /api/health needs a signed-in user, live questions stop
when the day's budget is spent, and each user may ask only so many questions an hour.

Everything is kept in one SQLite file (CONSOLE_DB, default checkpoints/console.sqlite, which is a
volume on the server so it survives deploys). Accounts are created by the operator from the
command line (python -m app.api.users add <name>); there is no sign-up page.

Stored safely: passwords as scrypt hashes with a random salt, sessions and API keys as SHA-256
hashes, so a copy of the database file signs nobody in.

API keys let other applications (scripts, spreadsheets, MCP clients) act as the user who created
them: same budget, same limits, same audit log. A key is shown once, when it is created.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
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
API_KEY_PREFIX = "ak_"  # recognisable in logs and secret scanners; the part after it is random
SESSION_HOURS = 12
FAILED_LOGINS_ALLOWED = 5  # per user name, within FAILED_LOGIN_WINDOW_S; then wait
FAILED_LOGIN_WINDOW_S = 15 * 60
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
ROLES = ("user", "admin")  # admins invite people, reset passwords and remove accounts
USER_NAME = re.compile(r"^[A-Za-z0-9._-]{1,40}$")

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
create table if not exists api_keys (
    key_hash text primary key, prefix text not null unique, user_name text not null, name text not null,
    created_at real not null, last_used_at real);
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
            self._migrate(db)

    @staticmethod
    def _migrate(db: sqlite3.Connection) -> None:
        """Bring a database made by an older version up to date, without losing anything.

        Roles arrived after the first accounts: if no admin exists yet, the oldest account becomes one,
        so whoever set up the console can invite the others from the Admin page.
        """
        columns = {row["name"] for row in db.execute("pragma table_info(users)")}
        if "role" not in columns:
            db.execute("alter table users add column role text not null default 'user'")
        if "must_change" not in columns:
            db.execute("alter table users add column must_change integer not null default 0")
        if not db.execute("select 1 from users where role = 'admin'").fetchone():
            db.execute("update users set role = 'admin' where name = "
                       "(select name from users order by created_at limit 1)")

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

    def add_user(self, name: str, password: str, role: str | None = None, must_change: bool = False) -> None:
        """Create an account, or give an existing one a new password (its role is kept unless given).

        The very first account is an admin, so a new console always has someone who can invite others.
        """
        if not USER_NAME.match(name):
            raise ValueError("Use 1 to 40 letters, digits, dots, dashes or underscores for the name.")
        if len(password) < 10:
            raise ValueError("Use a password of at least 10 characters.")
        if role is not None and role not in ROLES:
            raise ValueError(f"The role must be one of {', '.join(ROLES)}.")
        with self._db() as db:
            first = not db.execute("select 1 from users").fetchone()
            db.execute("insert into users (name, password_hash, created_at, role, must_change) values (?, ?, ?, ?, ?) "
                       "on conflict(name) do update set password_hash = excluded.password_hash, "
                       "must_change = excluded.must_change" + (", role = excluded.role" if role else ""),
                       (name, hash_password(password), time.time(), role or ("admin" if first else "user"),
                        int(must_change)))
            db.execute("delete from sessions where user_name = ?", (name,))  # a new password signs out everywhere

    def invite(self, name: str, role: str = "user") -> str:
        """A new account with a one-time password; its owner must choose their own at first sign-in."""
        if self.exists(name):
            raise ValueError(f"An account called '{name}' already exists.")
        temporary = secrets.token_urlsafe(9)
        self.add_user(name, temporary, role=role, must_change=True)
        return temporary

    def reset_password(self, name: str) -> str:
        if not self.exists(name):
            raise ValueError(f"No account called '{name}'.")
        temporary = secrets.token_urlsafe(9)
        self.add_user(name, temporary, must_change=True)
        return temporary

    def change_password(self, name: str, current: str, new: str) -> bool:
        """The owner's own change: the current password must be right. Signs out every other session."""
        with self._db() as db:
            row = db.execute("select password_hash from users where name = ?", (name,)).fetchone()
        if not row or not check_password(current, row["password_hash"]):
            return False
        if new == current:
            raise ValueError("Choose a password different from the current one.")
        self.add_user(name, new, must_change=False)
        return True

    def exists(self, name: str) -> bool:
        with self._db() as db:
            return db.execute("select 1 from users where name = ?", (name,)).fetchone() is not None

    def account(self, name: str) -> dict | None:
        with self._db() as db:
            row = db.execute("select name, role, must_change, created_at from users where name = ?", (name,)).fetchone()
            return {**dict(row), "must_change": bool(row["must_change"])} if row else None

    def set_role(self, name: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"The role must be one of {', '.join(ROLES)}.")
        with self._db() as db:
            others = db.execute("select count(*) from users where role = 'admin' and name != ?", (name,)).fetchone()[0]
            if role == "user" and others == 0:
                raise ValueError("Keep at least one admin.")
            db.execute("update users set role = ? where name = ?", (role, name))

    def accounts(self) -> list[dict]:
        """Every account with its role and activity, for the Admin page."""
        with self._db() as db:
            rows = db.execute(
                "select u.name, u.role, u.must_change, u.created_at, max(q.asked_at) as last_asked, "
                "coalesce(sum(case when q.day = ? then q.tokens end), 0) as tokens_today "
                "from users u left join questions q on q.user_name = u.name group by u.name order by u.name",
                (today(),))
            return [{**dict(row), "must_change": bool(row["must_change"])} for row in rows]

    def remove_user(self, name: str) -> bool:
        with self._db() as db:
            db.execute("delete from sessions where user_name = ?", (name,))
            db.execute("delete from api_keys where user_name = ?", (name,))
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

    # ---- API keys --------------------------------------------------------------------------

    def create_key(self, user: str, name: str) -> dict:
        """A new key for `user`. The full key is returned only here; only its hash is stored."""
        secret = API_KEY_PREFIX + secrets.token_urlsafe(32)
        prefix = secret[: len(API_KEY_PREFIX) + 8]  # enough to tell keys apart in a list, useless to sign in
        with self._db() as db:
            db.execute("insert into api_keys values (?, ?, ?, ?, ?, null)",
                       (_token_hash(secret), prefix, user, name.strip() or "Unnamed key", time.time()))
        return {"key": secret, "prefix": prefix, "name": name}

    def user_for_key(self, key: str | None) -> str | None:
        if not key or not key.startswith(API_KEY_PREFIX):
            return None
        with self._db() as db:
            row = db.execute("select user_name from api_keys where key_hash = ?", (_token_hash(key),)).fetchone()
            if row:
                db.execute("update api_keys set last_used_at = ? where key_hash = ?", (time.time(), _token_hash(key)))
            return row["user_name"] if row else None

    def keys(self, user: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute("select prefix, name, created_at, last_used_at from api_keys where user_name = ? "
                              "order by created_at desc", (user,))
            return [dict(row) for row in rows]

    def revoke_key(self, user: str, prefix: str) -> bool:
        with self._db() as db:
            return db.execute("delete from api_keys where user_name = ? and prefix = ?", (user, prefix)).rowcount > 0

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

    def log_reused(self, user: str, question: str, trace_id: str | None) -> None:
        """A saved answer was shown again: logged, but it spent nothing and counts against no limit."""
        with self._db() as db:
            db.execute("insert into questions (user_name, question, asked_at, day, status, trace_id) "
                       "values (?, ?, ?, ?, 'reused', ?)", (user, question, time.time(), today(), trace_id))

    def recent_questions(self, limit: int = 500) -> list[str]:
        """Questions that got an answer, newest first, from every user (for suggestions)."""
        with self._db() as db:
            rows = db.execute("select question from questions where status in ('answered', 'reused') "
                              "order by asked_at desc limit ?", (limit,))
            return [row["question"] for row in rows]

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
