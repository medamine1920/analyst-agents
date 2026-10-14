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
import json
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app import crypto
from app.api import twofactor
from app.db import connect

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
SEEN_EVERY_S = 60  # how often a session's "last seen" is written, to keep reads cheap
# Passwords people pick first; refused whatever their length.
COMMON_PASSWORDS = {"password", "password1", "password123", "1234567890", "12345678910", "qwertyuiop",
                    "iloveyou12", "letmein123", "welcome123", "changeme12", "administrator", "abcdefghij",
                    "passw0rd12", "0123456789", "1q2w3e4r5t", "qwerty1234", "football12", "monkey1234"}

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
create table if not exists auth_events (
    user_name text not null, at real not null, kind text not null, ip text, user_agent text);
create index if not exists auth_events_by_user on auth_events (user_name, at);
create table if not exists api_keys (
    key_hash text primary key, prefix text not null unique, user_name text not null, name text not null,
    created_at real not null, last_used_at real);
"""


class TwoFactorRequired(Exception):
    """The password was right, but this account also needs the code from its authenticator app."""


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


def password_problem(name: str, password: str) -> str | None:
    """Why a new password is too weak, or None."""
    if len(password) < 10:
        return "Use a password of at least 10 characters."
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or len(set(lowered)) < 4:
        return "That password is too easy to guess. A short sentence works well."
    if name and len(name) >= 3 and name.lower() in lowered:
        return "Leave your name out of your password."
    return None


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
        self.key_file = self.path.parent / ".console_key"  # for two-step sign-in secrets (see app/crypto.py)
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
        for table, column, definition in (
            ("questions", "source", "text not null default 'sample'"),
            ("questions", "project", "text"),
            # Questions run on a user's own model key are logged with that model and kept out of the budget.
            ("questions", "model", "text"), ("questions", "own_key", "integer not null default 0"),
            ("sessions", "created_at", "real"), ("sessions", "last_seen", "real"),
            ("sessions", "ip", "text"), ("sessions", "user_agent", "text"),
            ("users", "totp_secret", "text"), ("users", "totp_pending", "text"), ("users", "recovery", "text"),
            ("api_keys", "expires_at", "real"),
        ):
            if column not in {row["name"] for row in db.execute(f"pragma table_info({table})")}:
                db.execute(f"alter table {table} add column {column} {definition}")
        if not db.execute("select 1 from users where role = 'admin'").fetchone():
            db.execute("update users set role = 'admin' where name = "
                       "(select name from users order by created_at limit 1)")

    def _db(self):
        return connect(self.path)

    # ---- accounts ------------------------------------------------------------------------

    def add_user(self, name: str, password: str, role: str | None = None, must_change: bool = False) -> None:
        """Create an account, or give an existing one a new password (its role is kept unless given).

        The very first account is an admin, so a new console always has someone who can invite others.
        """
        if not USER_NAME.match(name):
            raise ValueError("Use 1 to 40 letters, digits, dots, dashes or underscores for the name.")
        problem = password_problem(name, password)
        if problem:
            raise ValueError(problem)
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

    def record(self, user: str, kind: str, ip: str = "", user_agent: str = "") -> None:
        """Account activity a user can review: sign-ins, failures, password and two-step changes."""
        with self._db() as db:
            db.execute("insert into auth_events values (?, ?, ?, ?, ?)", (user, time.time(), kind, ip, user_agent[:200]))
            db.execute("delete from auth_events where at < ?", (time.time() - 90 * 86400,))  # keep 90 days

    def events(self, user: str, limit: int = 30) -> list[dict]:
        with self._db() as db:
            rows = db.execute("select at, kind, ip, user_agent from auth_events where user_name = ? "
                              "order by at desc limit ?", (user, limit))
            return [dict(row) for row in rows]

    def sign_in(self, name: str, password: str, code: str | None = None, ip: str = "", user_agent: str = "") -> str | None:
        """A new session token, or None. Repeated failures lock the name for a while.

        With two-step sign-in on, a right password without a right code raises TwoFactorRequired; the
        code can also be one of the account's recovery codes, which then stops working.
        """
        now = time.time()
        with self._db() as db:
            recent = db.execute("select count(*) from failed_logins where user_name = ? and at > ?",
                                (name, now - FAILED_LOGIN_WINDOW_S)).fetchone()[0]
            if recent >= FAILED_LOGINS_ALLOWED:
                raise LimitReached("Too many failed sign-ins. Try again in 15 minutes.")
            row = db.execute("select password_hash, totp_secret, recovery from users where name = ?", (name,)).fetchone()
            # Hash even for an unknown name, so the response time does not reveal which names exist.
            valid = check_password(password, row["password_hash"] if row else hash_password("x"))
            if not (row and valid):
                db.execute("insert into failed_logins values (?, ?)", (name, now))
        if not (row and valid):
            if row:
                self.record(name, "failed sign-in", ip, user_agent)
            return None
        if row["totp_secret"]:
            if not code:
                raise TwoFactorRequired("Enter the 6-digit code from your authenticator app.")
            if not self._second_step(name, row, code):
                with self._db() as db:
                    db.execute("insert into failed_logins values (?, ?)", (name, now))
                self.record(name, "wrong two-step code", ip, user_agent)
                raise TwoFactorRequired("That code is not right. Codes change every 30 seconds.")
        with self._db() as db:
            db.execute("delete from failed_logins where user_name = ?", (name,))
            db.execute("delete from sessions where expires_at < ?", (now,))
            token = secrets.token_urlsafe(32)
            db.execute("insert into sessions (token_hash, user_name, expires_at, created_at, last_seen, ip, user_agent) "
                       "values (?, ?, ?, ?, ?, ?, ?)",
                       (_token_hash(token), name, now + SESSION_HOURS * 3600, now, now, ip, user_agent[:200]))
        self.record(name, "signed in", ip, user_agent)
        return token

    def issue_session(self, name: str, ip: str = "", user_agent: str = "") -> str:
        """A session for someone already verified (e.g. right after changing their password)."""
        now, token = time.time(), secrets.token_urlsafe(32)
        with self._db() as db:
            db.execute("insert into sessions (token_hash, user_name, expires_at, created_at, last_seen, ip, user_agent) "
                       "values (?, ?, ?, ?, ?, ?, ?)",
                       (_token_hash(token), name, now + SESSION_HOURS * 3600, now, now, ip, user_agent[:200]))
        return token

    def _second_step(self, name: str, row: sqlite3.Row, code: str) -> bool:
        if twofactor.check_code(crypto.unseal(row["totp_secret"], self.key_file), code):
            return True
        remaining = json.loads(row["recovery"] or "[]")
        hashed = twofactor.hash_code(code)
        if hashed in remaining:  # a recovery code works once
            remaining.remove(hashed)
            with self._db() as db:
                db.execute("update users set recovery = ? where name = ?", (json.dumps(remaining), name))
            self.record(name, "used a recovery code")
            return True
        return False

    def user_for(self, token: str | None) -> str | None:
        if not token:
            return None
        now = time.time()
        with self._db() as db:
            row = db.execute("select user_name, last_seen from sessions where token_hash = ? and expires_at > ?",
                             (_token_hash(token), now)).fetchone()
            if row and (row["last_seen"] or 0) < now - SEEN_EVERY_S:
                db.execute("update sessions set last_seen = ? where token_hash = ?", (now, _token_hash(token)))
            return row["user_name"] if row else None

    def sign_out(self, token: str | None) -> None:
        if token:
            with self._db() as db:
                db.execute("delete from sessions where token_hash = ?", (_token_hash(token),))

    def sessions(self, user: str, current: str | None = None) -> list[dict]:
        """Where this account is signed in. Ids are a short part of the stored hash, never the cookie."""
        current_hash = _token_hash(current) if current else None
        with self._db() as db:
            rows = db.execute("select token_hash, created_at, last_seen, ip, user_agent from sessions "
                              "where user_name = ? and expires_at > ? order by last_seen desc", (user, time.time()))
            return [{"id": r["token_hash"][:16], "created_at": r["created_at"], "last_seen": r["last_seen"],
                     "ip": r["ip"], "user_agent": r["user_agent"], "current": r["token_hash"] == current_hash}
                    for r in rows]

    def end_sessions(self, user: str, session_id: str | None = None, keep: str | None = None) -> int:
        """Sign out one session (by id), or every session except `keep` (the one asking)."""
        keep_hash = _token_hash(keep) if keep else ""
        with self._db() as db:
            if session_id:
                return db.execute("delete from sessions where user_name = ? and substr(token_hash, 1, 16) = ? "
                                  "and token_hash != ?", (user, session_id, keep_hash)).rowcount
            return db.execute("delete from sessions where user_name = ? and token_hash != ?", (user, keep_hash)).rowcount

    # ---- two-step sign-in -------------------------------------------------------------------

    def two_factor_on(self, user: str) -> bool:
        with self._db() as db:
            row = db.execute("select totp_secret from users where name = ?", (user,)).fetchone()
            return bool(row and row["totp_secret"])

    def start_two_factor(self, user: str) -> dict:
        """A new secret, waiting for a first right code before it counts."""
        secret = twofactor.new_secret()
        with self._db() as db:
            db.execute("update users set totp_pending = ? where name = ?", (crypto.seal(secret, self.key_file), user))
        return {"secret": secret, "uri": twofactor.setup_uri(secret, user)}

    def finish_two_factor(self, user: str, code: str) -> list[str] | None:
        """Turn it on if the code matches the pending secret; returns the recovery codes, shown once."""
        with self._db() as db:
            row = db.execute("select totp_pending from users where name = ?", (user,)).fetchone()
        if not row or not row["totp_pending"]:
            return None
        if not twofactor.check_code(crypto.unseal(row["totp_pending"], self.key_file), code):
            return None
        codes = twofactor.recovery_codes()
        with self._db() as db:
            db.execute("update users set totp_secret = totp_pending, totp_pending = null, recovery = ? where name = ?",
                       (json.dumps([twofactor.hash_code(c) for c in codes]), user))
        self.record(user, "turned on two-step sign-in")
        return codes

    def stop_two_factor(self, user: str, password: str) -> bool:
        with self._db() as db:
            row = db.execute("select password_hash from users where name = ?", (user,)).fetchone()
        if not row or not check_password(password, row["password_hash"]):
            return False
        with self._db() as db:
            db.execute("update users set totp_secret = null, totp_pending = null, recovery = null where name = ?", (user,))
        self.record(user, "turned off two-step sign-in")
        return True

    # ---- API keys --------------------------------------------------------------------------

    def create_key(self, user: str, name: str, days: int | None = None) -> dict:
        """A new key for `user`, optionally expiring after `days`. Returned in full only here; only its hash is kept."""
        secret = API_KEY_PREFIX + secrets.token_urlsafe(32)
        prefix = secret[: len(API_KEY_PREFIX) + 8]  # enough to tell keys apart in a list, useless to sign in
        expires = time.time() + days * 86400 if days else None
        with self._db() as db:
            db.execute("insert into api_keys (key_hash, prefix, user_name, name, created_at, last_used_at, expires_at) "
                       "values (?, ?, ?, ?, ?, null, ?)",
                       (_token_hash(secret), prefix, user, name.strip() or "Unnamed key", time.time(), expires))
        self.record(user, f"created API key {prefix}…")
        return {"key": secret, "prefix": prefix, "name": name, "expires_at": expires}

    def user_for_key(self, key: str | None) -> str | None:
        if not key or not key.startswith(API_KEY_PREFIX):
            return None
        with self._db() as db:
            row = db.execute("select user_name from api_keys where key_hash = ? and (expires_at is null or expires_at > ?)",
                             (_token_hash(key), time.time())).fetchone()
            if row:
                db.execute("update api_keys set last_used_at = ? where key_hash = ?", (time.time(), _token_hash(key)))
            return row["user_name"] if row else None

    def keys(self, user: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute("select prefix, name, created_at, last_used_at, expires_at from api_keys where user_name = ? "
                              "order by created_at desc", (user,))
            return [dict(row) for row in rows]

    def revoke_key(self, user: str, prefix: str) -> bool:
        with self._db() as db:
            return db.execute("delete from api_keys where user_name = ? and prefix = ?", (user, prefix)).rowcount > 0

    # ---- budget, limits and the audit log ------------------------------------------------

    def tokens_today(self) -> int:
        with self._db() as db:
            return db.execute("select coalesce(sum(tokens), 0) from questions where day = ? and own_key = 0",
                              (today(),)).fetchone()[0]

    def check_budget(self, limits: Limits) -> None:
        if self.tokens_today() >= limits.daily_tokens:
            raise LimitReached(f"Today's budget of {limits.daily_tokens:,} model tokens is used up. "
                               "Live questions start again at midnight UTC; saved runs still replay.")

    def start_question(self, user: str, question: str, limits: Limits, thread_id: str, source: str = "sample",
                       project: str | None = None, model: str | None = None, own_key: bool = False) -> int:
        """Check the limits, then log the question. Raises LimitReached before anything is spent.

        A question on the user's own key is not counted in the shared budget (their provider bills it),
        but the hourly limit still applies: it protects the console, not the quota.
        """
        now = time.time()
        if not own_key:
            self.check_budget(limits)
        with self._db() as db:
            asked = db.execute("select count(*) from questions where user_name = ? and asked_at > ?",
                               (user, now - 3600)).fetchone()[0]
            if asked >= limits.questions_per_hour:
                raise LimitReached(f"You have asked {asked} questions in the last hour, the limit is "
                                   f"{limits.questions_per_hour}. Try again a little later.")
            return db.execute("insert into questions (user_name, question, asked_at, day, thread_id, source, project, "
                              "model, own_key) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (user, question, now, today(), thread_id, source, project, model,
                               int(own_key))).lastrowid

    def log_reused(self, user: str, question: str, trace_id: str | None, source: str = "sample",
                   project: str | None = None) -> None:
        """A saved answer was shown again: logged, but it spent nothing and counts against no limit."""
        with self._db() as db:
            db.execute("insert into questions (user_name, question, asked_at, day, status, trace_id, source, project) "
                       "values (?, ?, ?, ?, 'reused', ?, ?, ?)",
                       (user, question, time.time(), today(), trace_id, source, project))

    def recent_questions(self, limit: int = 500, source: str = "sample", user: str | None = None) -> list[str]:
        """Questions that got an answer, newest first, for suggestions.

        The sample shop's come from every user; a private source's only from its owner.
        """
        with self._db() as db:
            rows = db.execute("select question from questions where status in ('answered', 'reused') and source = ? "
                              + ("and user_name = ? " if user else "") + "order by asked_at desc limit ?",
                              (source, user, limit) if user else (source, limit))
            return [row["question"] for row in rows]

    def has_trace(self, user: str, trace_id: str) -> bool:
        """Whether this user asked a question answered by that run (their own, or a saved answer shown to them)."""
        with self._db() as db:
            return db.execute("select 1 from questions where user_name = ? and trace_id = ? limit 1",
                              (user, trace_id)).fetchone() is not None

    def question_for_thread(self, thread_id: str) -> int | None:
        with self._db() as db:
            row = db.execute("select id from questions where thread_id = ? order by id desc limit 1",
                             (thread_id,)).fetchone()
            return row["id"] if row else None

    def expire_waiting(self, asked_before: float) -> list[str]:
        """Mark questions paused (waiting for the user's choice) since before `asked_before` as expired.

        Returns their thread ids, so the caller can delete what the paused runs kept.
        """
        with self._db() as db:
            rows = db.execute("select thread_id from questions where status = 'waiting' and asked_at < ?",
                              (asked_before,)).fetchall()
            db.execute("update questions set status = 'expired' where status = 'waiting' and asked_at < ?",
                       (asked_before,))
        return [row["thread_id"] for row in rows if row["thread_id"]]

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

    def history(self, user: str | None = None, limit: int = 50, project: str | None = None) -> list[dict]:
        """Newest first; with `project`, only that project's questions ("none" for questions in no project)."""
        clauses, values = [], []
        if user:
            clauses.append("user_name = ?")
            values.append(user)
        if project == "none":
            clauses.append("project is null")
        elif project:
            clauses.append("project = ?")
            values.append(project)
        where = ("where " + " and ".join(clauses) + " ") if clauses else ""
        with self._db() as db:
            rows = db.execute("select user_name, question, asked_at, status, tokens, trace_id, source, project, model "
                              f"from questions {where}order by asked_at desc limit ?", (*values, limit))
            return [dict(row) for row in rows]
