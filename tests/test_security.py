"""Sign-in, sessions, budget and limits for the public console."""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.security import (
    LimitReached,
    Limits,
    SecurityStore,
    check_password,
    hash_password,
)
from app.mcp_server.server import server
from tests.test_api import plan_fn, write_fn

PASSWORD = "correct horse battery"


@pytest.fixture
def store(tmp_path):
    store = SecurityStore(tmp_path / "console.sqlite")
    store.add_user("ana", PASSWORD)
    return store


def test_passwords_are_salted_hashes_and_check_out():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second and PASSWORD not in first  # a new salt each time, never the password itself
    assert check_password(PASSWORD, first) and not check_password("wrong password!", first)
    assert not check_password(PASSWORD, "not-a-hash")


def test_short_passwords_are_refused(store):
    with pytest.raises(ValueError):
        store.add_user("bob", "short")


def test_sessions_are_stored_as_hashes_and_end_on_sign_out(store):
    token = store.sign_in("ana", PASSWORD)
    assert store.user_for(token) == "ana" and store.user_for("forged") is None
    raw = store.path.read_bytes()
    assert token.encode() not in raw  # a copy of the database file signs nobody in
    store.sign_out(token)
    assert store.user_for(token) is None


def test_repeated_wrong_passwords_lock_the_name(store):
    for _ in range(5):
        assert store.sign_in("ana", "wrong password!") is None
    with pytest.raises(LimitReached):
        store.sign_in("ana", PASSWORD)  # even the right password waits


def test_changing_a_password_signs_out_everywhere(store):
    token = store.sign_in("ana", PASSWORD)
    store.add_user("ana", "a brand new password")
    assert store.user_for(token) is None


def test_the_daily_budget_and_the_hourly_limit(store):
    limits = Limits(daily_tokens=5000, questions_per_hour=2)
    first = store.start_question("ana", "q1", limits, "t1")
    store.finish_question(first, "answered", tokens=4000)
    store.start_question("ana", "q2", limits, "t2")
    with pytest.raises(LimitReached, match="last hour"):
        store.start_question("ana", "q3", limits, "t3")
    store.finish_question(store.question_for_thread("t2"), "answered", tokens=1500)
    with pytest.raises(LimitReached, match="budget"):
        store.start_question("someone else", "q4", limits, "t4")  # the budget is shared by everyone
    assert store.usage("ana", limits)["tokens_left"] == 0


# ---- through the API ------------------------------------------------------------------------

@pytest.fixture
def api(tmp_path):
    store = SecurityStore(tmp_path / "console.sqlite")
    store.add_user("ana", PASSWORD)
    app = create_app(mcp_target=server, plan_fn=plan_fn, write_fn=write_fn, traces_dir=tmp_path / "traces",
                     checkpoint_db=tmp_path / "c.sqlite", results_dir=tmp_path, auth=True,
                     security_db=tmp_path / "console.sqlite", limits=Limits(daily_tokens=6000, questions_per_hour=10))
    with TestClient(app) as client:
        yield client


def events_of(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.mark.integration
def test_every_route_needs_sign_in_except_health(api):
    assert api.get("/api/health").status_code == 200
    for path in ("/api/metrics", "/api/traces", "/api/evals", "/api/domain", "/api/history"):
        assert api.get(path).status_code == 401, path
    assert api.post("/api/ask", json={"question": "How many orders?"}).status_code == 401
    assert api.get("/api/auth/me").json()["user"] is None


@pytest.mark.integration
def test_sign_in_ask_and_see_the_budget_go_down(api):
    assert api.post("/api/auth/login", json={"name": "ana", "password": "wrong password!"}).status_code == 401
    signed_in = api.post("/api/auth/login", json={"name": "ana", "password": PASSWORD})
    assert signed_in.status_code == 200
    cookie = signed_in.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    events = events_of(api.post("/api/ask", json={"question": "How many orders were placed?"}))
    assert events[-1]["type"] == "saved"
    me = api.get("/api/auth/me").json()
    assert me["user"] == "ana" and me["usage"]["tokens_today"] == 4250  # what the scripted model reported
    history = api.get("/api/history").json()
    assert history[0]["question"] == "How many orders were placed?" and history[0]["status"] == "answered"
    # 4,250 of 6,000 used: the next question is still allowed, the one after that is refused.
    # ("fresh" skips the saved answer, which would otherwise answer the repeat for free.)
    events_of(api.post("/api/ask", json={"question": "How many orders were placed?", "fresh": True}))
    refused = api.post("/api/ask", json={"question": "How many orders were placed?", "fresh": True})
    assert refused.status_code == 429 and "budget" in refused.json()["detail"]
    api.post("/api/auth/logout")
    assert api.get("/api/metrics").status_code == 401


def test_invited_accounts_must_choose_their_own_password(store):
    temporary = store.invite("bob")
    assert store.account("bob") == {**store.account("bob"), "role": "user", "must_change": True}
    assert store.sign_in("bob", temporary)
    assert not store.change_password("bob", "wrong password!", "a quiet harbour at dawn")
    assert store.change_password("bob", temporary, "a quiet harbour at dawn")
    assert not store.account("bob")["must_change"] and store.sign_in("bob", temporary) is None
    with pytest.raises(ValueError):
        store.invite("bob")  # already exists
    with pytest.raises(ValueError):
        store.invite("bad name!")


def test_the_first_account_is_an_admin_and_one_admin_always_remains(tmp_path):
    store = SecurityStore(tmp_path / "fresh.sqlite")
    store.add_user("first", PASSWORD)
    store.add_user("second", PASSWORD)
    assert store.account("first")["role"] == "admin" and store.account("second")["role"] == "user"
    with pytest.raises(ValueError, match="at least one admin"):
        store.set_role("first", "user")
    store.add_user("first", "a brand new password")
    assert store.account("first")["role"] == "admin"  # a new password keeps the role


def test_an_older_database_gets_roles_and_an_admin(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute("create table users (name text primary key, password_hash text not null, created_at real not null)")
    db.execute("insert into users values ('amine', ?, 1)", (hash_password(PASSWORD),))
    db.commit()
    db.close()
    store = SecurityStore(path)
    assert store.account("amine")["role"] == "admin" and store.sign_in("amine", PASSWORD)


@pytest.mark.integration
def test_admins_manage_accounts_and_everyone_changes_their_own_password(api):
    api.post("/api/auth/login", json={"name": "ana", "password": PASSWORD})  # ana is the first account: admin
    assert api.get("/api/auth/me").json()["role"] == "admin"
    invited = api.post("/api/admin/users", json={"name": "bob"}).json()
    assert {a["name"] for a in api.get("/api/admin/users").json()} == {"ana", "bob"}
    api.post("/api/auth/logout")

    api.post("/api/auth/login", json={"name": "bob", "password": invited["temporary_password"]})
    assert api.get("/api/auth/me").json()["must_change_password"]
    assert api.get("/api/admin/users").status_code == 403  # not an admin
    wrong = api.post("/api/account/password", json={"current": "nope", "new": "a quiet harbour at dawn"})
    assert wrong.status_code == 403
    assert api.post("/api/account/password", json={"current": invited["temporary_password"],
                                                   "new": "a quiet harbour at dawn"}).status_code == 200
    me = api.get("/api/auth/me").json()
    assert me["user"] == "bob" and not me["must_change_password"]  # still signed in, with a new cookie


def test_weak_passwords_are_refused(store):
    for weak, reason in (("short", "10 characters"), ("password123", "easy to guess"), ("aaaaaaaaaaaa", "easy to guess"),
                         ("ana-is-great-2025", "name out")):
        with pytest.raises(ValueError, match=reason):
            store.add_user("ana", weak)


def test_two_step_sign_in_with_an_authenticator_and_recovery_codes(store):
    import time as clock

    from app.api import twofactor
    from app.api.security import TwoFactorRequired

    setup = store.start_two_factor("ana")
    assert setup["uri"].startswith("otpauth://totp/") and not store.two_factor_on("ana")
    assert store.finish_two_factor("ana", "000000") is None  # a wrong first code turns nothing on
    codes = store.finish_two_factor("ana", twofactor.code_at(setup["secret"], clock.time()))
    assert len(codes) == 8 and store.two_factor_on("ana")
    with pytest.raises(TwoFactorRequired, match="6-digit"):
        store.sign_in("ana", PASSWORD)
    with pytest.raises(TwoFactorRequired, match="not right"):
        store.sign_in("ana", PASSWORD, "123456")
    assert store.sign_in("ana", PASSWORD, twofactor.code_at(setup["secret"], clock.time()))
    assert store.sign_in("ana", PASSWORD, codes[0])  # a recovery code works once
    with pytest.raises(TwoFactorRequired):
        store.sign_in("ana", PASSWORD, codes[0])
    assert setup["secret"].encode() not in store.path.read_bytes()  # the secret is stored encrypted
    assert store.stop_two_factor("ana", PASSWORD) and store.sign_in("ana", PASSWORD)


def test_sessions_are_listed_and_can_be_ended_from_elsewhere(store):
    here = store.sign_in("ana", PASSWORD, ip="1.2.3.4", user_agent="Firefox")
    there = store.sign_in("ana", PASSWORD, ip="5.6.7.8", user_agent="Phone")
    listed = store.sessions("ana", here)
    assert {s["ip"] for s in listed} == {"1.2.3.4", "5.6.7.8"} and sum(s["current"] for s in listed) == 1
    other = next(s for s in listed if not s["current"])
    assert store.end_sessions("ana", other["id"], keep=here) == 1
    assert store.user_for(there) is None and store.user_for(here) == "ana"
    assert [e["kind"] for e in store.events("ana")][:2] == ["signed in", "signed in"]


def test_api_keys_can_expire(store):
    import time as clock

    key = store.create_key("ana", "Short-lived", days=1)
    assert store.user_for_key(key["key"]) == "ana"
    with store._db() as db:
        db.execute("update api_keys set expires_at = ? where prefix = ?", (clock.time() - 1, key["prefix"]))
    assert store.user_for_key(key["key"]) is None


@pytest.mark.integration
def test_headers_projects_and_admin_only_evaluations(api):
    response = api.get("/api/health")
    assert response.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in response.headers[
        "content-security-policy"] and response.headers["cache-control"] == "no-store"
    api.post("/api/auth/login", json={"name": "ana", "password": PASSWORD})
    project = api.post("/api/projects", json={"name": "Q3 review"}).json()
    events = api.post("/api/ask", json={"question": "How many orders were placed?", "project": project["id"]})
    assert events.status_code == 200
    assert api.get("/api/history", params={"project": project["id"]}).json()[0]["project"] == project["id"]
    assert api.get("/api/projects").json()[0]["questions"] == 1
    assert api.patch(f"/api/projects/{project['id']}", json={"name": "Q3 sales review"}).json()["name"] == "Q3 sales review"
    assert api.post("/api/ask", json={"question": "How many orders?", "project": "ffffffffff"}).status_code == 404
    assert api.get("/api/evals").status_code == 200  # ana is the admin
    invited = api.post("/api/admin/users", json={"name": "bob"}).json()
    api.post("/api/auth/logout")
    api.post("/api/auth/login", json={"name": "bob", "password": invited["temporary_password"]})
    assert api.get("/api/evals").status_code == 403  # development figures are for admins
    assert api.get("/api/projects").json() == []  # projects are private
    assert api.delete(f"/api/projects/{project['id']}").status_code == 404
