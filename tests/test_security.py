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
    events_of(api.post("/api/ask", json={"question": "How many orders were placed?"}))
    refused = api.post("/api/ask", json={"question": "How many orders were placed?"})
    assert refused.status_code == 429 and "budget" in refused.json()["detail"]
    api.post("/api/auth/logout")
    assert api.get("/api/metrics").status_code == 401
