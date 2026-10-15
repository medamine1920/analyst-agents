"""Opening the console to testers: invite codes, sign-up, a daily limit per person, and feedback."""

import json

import pytest
from fastapi.testclient import TestClient

from app.agents.orchestrated.plan import Plan
from app.api.main import create_app
from app.api.security import Limits, SecurityStore
from app.mcp_server.server import server

PASSWORD = "correct horse battery"
TESTER = "a quiet blue river"
PLAN = Plan.model_validate({"question_type": "lookup", "steps": [
    {"id": "orders", "purpose": "Count all orders", "metrics": ["order_count"]}]})


async def plan_fn(messages):
    return PLAN.model_copy(deep=True), None, {"input_tokens": 3000, "output_tokens": 200}


async def write_fn(messages):
    return "There were 61,948 orders.", {"input_tokens": 1000, "output_tokens": 50}


@pytest.fixture
def client(tmp_path):
    SecurityStore(tmp_path / "console.sqlite").add_user("admin", PASSWORD)  # the first account is the admin
    app = create_app(mcp_target=server, plan_fn=plan_fn, write_fn=write_fn, traces_dir=tmp_path / "traces",
                     checkpoint_db=tmp_path / "c.sqlite", results_dir=tmp_path, auth=True,
                     security_db=tmp_path / "console.sqlite", sources_dir=tmp_path / "sources",
                     limits=Limits(daily_tokens=100_000, questions_per_hour=20, user_daily_tokens=6000))
    with TestClient(app) as test_client:
        yield test_client


def sign_in(client, name, password=PASSWORD):
    client.cookies.clear()
    return client.post("/api/auth/login", json={"name": name, "password": password})


def test_an_invite_code_lets_testers_create_their_own_accounts_until_it_is_used_up_or_off(client):
    sign_in(client, "admin")
    made = client.post("/api/admin/invites", json={"label": "LinkedIn beta", "max_uses": 2, "days": 30}).json()
    code = made["code"]
    assert code.startswith("BETA-") and code not in json.dumps(client.get("/api/admin/invites").json())

    client.cookies.clear()
    joined = client.post("/api/auth/signup", json={"code": code.lower(), "name": "sara", "password": TESTER})
    assert joined.status_code == 200 and client.get("/api/auth/me").json()["user"] == "sara"  # signed straight in
    assert client.get("/api/auth/me").json()["role"] == "user"

    client.cookies.clear()
    taken = client.post("/api/auth/signup", json={"code": code, "name": "sara", "password": TESTER})
    assert taken.status_code == 422 and "taken" in taken.json()["detail"]
    weak = client.post("/api/auth/signup", json={"code": code, "name": "omar", "password": "aaaaaaaaaaaa"})
    assert weak.status_code == 422  # a weak password does not use up a place
    assert client.post("/api/auth/signup", json={"code": code, "name": "omar", "password": TESTER}).status_code == 200
    client.cookies.clear()
    full = client.post("/api/auth/signup", json={"code": code, "name": "lina", "password": TESTER})
    assert full.status_code == 422 and "not valid" in full.json()["detail"]  # two places, both used

    sign_in(client, "admin")
    second = client.post("/api/admin/invites", json={"label": "Friends", "max_uses": 5, "days": None}).json()
    assert client.delete(f"/api/admin/invites/{second['prefix']}").status_code == 200
    client.cookies.clear()
    off = client.post("/api/auth/signup", json={"code": second["code"], "name": "lina", "password": TESTER})
    assert off.status_code == 422
    sign_in(client, "sara", TESTER)
    assert client.get("/api/admin/invites").status_code == 403  # testers cannot make codes


@pytest.mark.integration
def test_each_tester_has_a_daily_share_and_can_leave_feedback_that_admins_read(client):
    sign_in(client, "admin")
    code = client.post("/api/admin/invites", json={"max_uses": 1}).json()["code"]
    client.cookies.clear()
    client.post("/api/auth/signup", json={"code": code, "name": "sara", "password": TESTER})

    events = client.post("/api/ask", json={"question": "How many orders were placed?", "fresh": True}).text
    trace_id = json.loads(next(line for line in events.splitlines() if '"saved"' in line)[6:])["id"]
    usage = client.get("/api/auth/me").json()["usage"]
    assert usage["my_tokens_today"] == 4250 and usage["my_daily_tokens"] == 6000
    client.post("/api/ask", json={"question": "How many orders in total?", "fresh": True})
    refused = client.post("/api/ask", json={"question": "And how many orders overall?", "fresh": True})
    assert refused.status_code == 429 and "your own model key" in refused.json()["detail"]

    assert client.post("/api/feedback", json={"trace_id": trace_id, "question": "How many orders were placed?",
                                              "rating": -1, "comment": "Wrong period"}).status_code == 200
    client.post("/api/feedback", json={"trace_id": trace_id, "rating": 1, "comment": "Actually right"})  # replaces
    assert client.get("/api/admin/feedback").status_code == 403
    sign_in(client, "admin")
    feedback = client.get("/api/admin/feedback").json()
    assert [(f["user_name"], f["rating"], f["comment"]) for f in feedback] == [("sara", 1, "Actually right")]


@pytest.mark.integration
def test_removing_an_account_removes_everything_linked_to_it(client, tmp_path):
    """The privacy page promises it: sources, connections, keys, projects, feedback, history and saved runs."""
    from app.api.beta import BetaStore
    from app.api.model_keys import ModelKeyStore
    from app.connectors.store import ConnectionStore

    sign_in(client, "admin")
    code = client.post("/api/admin/invites", json={"max_uses": 1}).json()["code"]
    client.cookies.clear()
    client.post("/api/auth/signup", json={"code": code, "name": "sara", "password": TESTER})
    source = client.post("/api/sources/csv", data={"name": "Mine"},
                         files=[("files", ("mine.csv", b"region,amount\nNorth,3\n", "text/csv"))]).json()
    client.post("/api/projects", json={"name": "Q3 review"})
    events = client.post("/api/ask", json={"question": "How many orders were placed?", "fresh": True}).text
    trace_id = json.loads(next(line for line in events.splitlines() if '"saved"' in line)[6:])["id"]
    client.post("/api/feedback", json={"trace_id": trace_id, "rating": 1})
    db = tmp_path / "console.sqlite"
    ConnectionStore(db).add("sara", "slack", "Team", {}, {"webhook_url": "https://hooks.slack.com/services/T/B/x"})
    ModelKeyStore(db).save("sara", "openai", "sk-test-0123456789", "gpt-5", ["gpt-5"])
    assert any(p.name.startswith(trace_id) for p in (tmp_path / "traces").glob("*.json"))
    assert (tmp_path / "sources" / source["id"]).exists()  # so its removal below is a real check

    sign_in(client, "admin")
    assert client.delete("/api/admin/users/sara").status_code == 200
    assert not (tmp_path / "sources" / source["id"]).exists() and not (tmp_path / "traces" / f"{trace_id}.json").exists()
    assert ConnectionStore(db).list("sara") == [] and ModelKeyStore(db).list("sara") == []
    assert BetaStore(db).feedback() == [] and client.get("/api/history").json() == []
    assert sign_in(client, "sara", TESTER).status_code == 401
