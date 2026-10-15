"""Opening the console to testers: invite codes they sign up with, and their feedback on answers.

Invite codes: an admin makes a code for a number of people and days; each tester creates their own
private account with it (a shared account would mix everyone's data and history). Only a hash of each
code is kept, and the admin can switch a code off at any time. Sign-up attempts are limited per address.

Feedback: a thumbs up or down and an optional comment on any answer, one per person and answer (a second
one replaces the first); admins read them all.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.db import connect

SCHEMA = """
create table if not exists invites (
    code_hash text primary key, prefix text not null unique, label text not null, created_by text not null,
    created_at real not null, expires_at real, max_uses integer not null, uses integer not null default 0,
    active integer not null default 1);
create table if not exists signups (ip text not null, at real not null);
create table if not exists feedback (
    user_name text not null, trace_id text not null, question text not null, rating integer not null,
    comment text not null default '', at real not null, primary key (user_name, trace_id));
"""
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I: codes are read and typed by people
SIGNUPS_PER_HOUR = 10  # per address, failed or not


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


class BetaStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self):
        return connect(self.path)

    # ---- invite codes ----------------------------------------------------------------------------

    def create_invite(self, admin: str, label: str, max_uses: int, days: int | None) -> dict:
        code = "BETA-" + "".join(secrets.choice(ALPHABET) for _ in range(8))
        expires = time.time() + days * 86400 if days else None
        with self._db() as db:
            db.execute("insert into invites (code_hash, prefix, label, created_by, created_at, expires_at, max_uses) "
                       "values (?, ?, ?, ?, ?, ?, ?)",
                       (_hash(code), code[:9], label.strip() or "Testers", admin, time.time(), expires, max_uses))
        return {"code": code, "prefix": code[:9], "label": label, "max_uses": max_uses, "expires_at": expires}

    def invites(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("select prefix, label, created_by, created_at, expires_at, max_uses, uses, active "
                              "from invites order by created_at desc").fetchall()
        return [dict(r) for r in rows]

    def switch_off(self, prefix: str) -> bool:
        with self._db() as db:
            return db.execute("update invites set active = 0 where prefix = ?", (prefix,)).rowcount > 0

    def redeem(self, code: str) -> str | None:
        """Use one place on a valid code; its prefix, or None if it is unknown, used up, expired or off."""
        with self._db() as db:
            found = db.execute("update invites set uses = uses + 1 where code_hash = ? and active = 1 and "
                               "uses < max_uses and (expires_at is null or expires_at > ?) returning prefix",
                               (_hash(code), time.time())).fetchone()
        return found["prefix"] if found else None

    def give_back(self, prefix: str) -> None:
        """A sign-up that failed after its code was taken (a name already used, a weak password)."""
        with self._db() as db:
            db.execute("update invites set uses = max(uses - 1, 0) where prefix = ?", (prefix,))

    def signup_allowed(self, ip: str) -> bool:
        now = time.time()
        with self._db() as db:
            db.execute("delete from signups where at < ?", (now - 3600,))
            recent = db.execute("select count(*) from signups where ip = ?", (ip,)).fetchone()[0]
            db.execute("insert into signups values (?, ?)", (ip, now))
        return recent < SIGNUPS_PER_HOUR

    # ---- feedback --------------------------------------------------------------------------------

    def give_feedback(self, user: str, trace_id: str, question: str, rating: int, comment: str) -> None:
        with self._db() as db:
            db.execute("insert into feedback values (?, ?, ?, ?, ?, ?) on conflict (user_name, trace_id) do update "
                       "set rating = excluded.rating, comment = excluded.comment, at = excluded.at",
                       (user, trace_id, question[:500], rating, comment.strip()[:2000], time.time()))

    def forget(self, user: str) -> None:
        with self._db() as db:
            db.execute("delete from feedback where user_name = ?", (user,))

    def feedback(self, limit: int = 500) -> list[dict]:
        with self._db() as db:
            rows = db.execute("select * from feedback order by at desc limit ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


class SignUpRequest(BaseModel):
    code: str = Field(min_length=6, max_length=40)
    name: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=10, max_length=200)


class InviteCodeRequest(BaseModel):
    label: str = Field(default="Testers", max_length=80, description="Who the code is for, e.g. 'LinkedIn beta'")
    max_uses: int = Field(default=30, ge=1, le=1000)
    days: int | None = Field(default=30, ge=1, le=365, description="Days until it stops working; null for never")


class FeedbackRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=200)
    question: str = Field(default="", max_length=500)
    rating: int = Field(ge=-1, le=1, description="1: helpful, -1: not right")
    comment: str = Field(default="", max_length=2000)


def add_beta_routes(app: FastAPI, store, beta: BetaStore, admin_only, client_of, start_session) -> None:
    @app.post("/api/auth/signup")
    async def sign_up(body: SignUpRequest, request: Request, response: Response) -> dict:
        """Create your own account with an invite code, then you are signed in."""
        ip, agent = client_of(request)
        if not beta.signup_allowed(ip):
            raise HTTPException(429, "Too many sign-up attempts from here. Try again in an hour.")
        name = body.name.strip()
        if store.exists(name):
            raise HTTPException(422, f"The name '{name}' is taken. Choose another one.")
        prefix = beta.redeem(body.code)
        if not prefix:
            raise HTTPException(422, "That invite code is not valid: it may be mistyped, used up, expired or switched off.")
        try:
            store.add_user(name, body.password, role="user")
        except ValueError as exc:
            beta.give_back(prefix)
            raise HTTPException(422, str(exc)) from exc
        store.record(name, f"signed up with invite {prefix}…", ip, agent)
        start_session(name, body.password, request, response)
        return {"user": name}

    @app.get("/api/admin/invites")
    async def list_invites(request: Request) -> list[dict]:
        admin_only(request)
        return beta.invites()

    @app.post("/api/admin/invites")
    async def create_invite(body: InviteCodeRequest, request: Request) -> dict:
        """A new code, shown in full this once: only its hash is kept."""
        admin_only(request)
        return beta.create_invite(request.state.user, body.label, body.max_uses, body.days)

    @app.delete("/api/admin/invites/{prefix}")
    async def switch_off(prefix: str, request: Request) -> dict:
        admin_only(request)
        if not beta.switch_off(prefix):
            raise HTTPException(404, "No invite code starts with that.")
        return {"switched_off": prefix}

    @app.post("/api/feedback")
    async def give_feedback(body: FeedbackRequest, request: Request) -> dict:
        beta.give_feedback(request.state.user, body.trace_id, body.question, body.rating, body.comment)
        return {"saved": True}

    @app.get("/api/admin/feedback")
    async def read_feedback(request: Request) -> list[dict]:
        admin_only(request)
        return beta.feedback()
