"""Connectors: API keys, the JSON endpoint for scripts and spreadsheets, and the MCP endpoint."""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.connectors import result_from_events
from app.api.main import create_app
from app.api.security import Limits, SecurityStore
from app.mcp_server.server import server
from tests.test_api import plan_fn, write_fn

PASSWORD = "correct horse battery"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_keys_are_shown_once_stored_as_hashes_and_can_be_revoked(tmp_path):
    store = SecurityStore(tmp_path / "console.sqlite")
    created = store.create_key("ana", "Sales sheet")
    key = created["key"]
    assert key.startswith("ak_") and created["prefix"] == key[:11]
    assert store.user_for_key(key) == "ana" and store.user_for_key("ak_forged") is None
    assert key.encode() not in store.path.read_bytes()  # only its hash is kept
    listed = store.keys("ana")
    assert listed[0]["name"] == "Sales sheet" and listed[0]["last_used_at"] is not None and "key" not in listed[0]
    assert not store.revoke_key("bob", created["prefix"])  # only the owner can revoke it
    assert store.revoke_key("ana", created["prefix"]) and store.user_for_key(key) is None


def test_a_run_becomes_one_json_result():
    events = [
        {"type": "start"},
        {"type": "step", "node": "analyze", "data": {"facts": {
            "definitions": {"order_count": "Number of orders placed."},
            "steps": [{"id": "s", "purpose": "Count", "period": "all dates in the data", "rows": [{"order_count": 61948}]}]}}},
        {"type": "step", "node": "review", "data": {"issues": []}},
        {"type": "done", "answer": "There were 61,948 orders.", "elapsed_s": 1.5,
         "usage": {"input_tokens": 4000, "output_tokens": 250}},
        {"type": "saved", "id": "trace-1"},
    ]
    result = result_from_events(events)
    assert result["answer"] == "There were 61,948 orders." and result["checked"] and result["tokens"] == 4250
    assert result["data"][0]["rows"] == [{"order_count": 61948}] and result["trace_id"] == "trace-1"
    assert result["error"] is None and not result["reused"]


@pytest.fixture
def connected(tmp_path):
    store = SecurityStore(tmp_path / "console.sqlite")
    store.add_user("ana", PASSWORD)
    app = create_app(mcp_target=server, plan_fn=plan_fn, write_fn=write_fn, traces_dir=tmp_path / "traces",
                     checkpoint_db=tmp_path / "c.sqlite", results_dir=tmp_path, auth=True,
                     security_db=tmp_path / "console.sqlite", limits=Limits(daily_tokens=100_000, questions_per_hour=10))
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"name": "ana", "password": PASSWORD})
        key = client.post("/api/keys", json={"name": "Tests"}).json()["key"]
        client.cookies.clear()  # from here on only the key: like a script or an MCP client
        yield client, {"Authorization": f"Bearer {key}"}, key


def sign_in(client):
    client.post("/api/auth/login", json={"name": "ana", "password": PASSWORD})


@pytest.mark.integration
def test_a_key_asks_for_json_and_cannot_manage_keys(connected):
    app, auth, key = connected
    result = app.post("/api/v1/ask", json={"question": "How many orders were placed?"}, headers=auth).json()
    assert result["answer"] == "There were 61,948 orders." and result["checked"]
    assert result["data"][0]["rows"] == [{"order_count": 61948}] and result["tokens"] == 4250
    again = app.post("/api/v1/ask", json={"question": "How many orders were placed?"}, headers=auth).json()
    assert again["reused"] and again["tokens"] == 0  # saved answers work for connectors too
    assert app.get("/api/metrics", headers=auth).status_code == 200
    assert app.get("/api/keys", headers=auth).status_code == 403
    assert app.post("/api/keys", json={"name": "x"}, headers=auth).status_code == 403
    assert app.post("/api/v1/ask", json={"question": "How many orders?"}).status_code == 401
    assert [h["status"] for h in app.get("/api/history", headers=auth).json()] == ["reused", "answered"]

    sign_in(app)  # revoking needs the console, not the key
    assert app.delete(f"/api/keys/{key[:11]}").status_code == 200
    app.cookies.clear()
    refused = app.post("/api/v1/ask", json={"question": "How many orders?"}, headers=auth)
    assert refused.status_code == 401 and "revoked" in refused.json()["detail"]


def rpc(client, headers, method, params=None, ident=1):
    body = {"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}}
    response = client.post("/mcp", json=body, headers={**MCP_HEADERS, **headers})
    return response.status_code, (response.json() if response.content else None)


@pytest.mark.integration
def test_mcp_clients_get_the_checked_analyst_with_a_key_only(connected):
    app, auth, _ = connected
    assert app.post("/mcp", json={}, headers=MCP_HEADERS).status_code == 401
    status, init = rpc(app, auth, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
    assert status == 200 and init["result"]["serverInfo"]["name"] == "analyst-console"
    _, tools = rpc(app, auth, "tools/list", ident=2)
    assert {t["name"] for t in tools["result"]["tools"]} == {"ask_analyst", "list_metrics"}
    _, called = rpc(app, auth, "tools/call", {"name": "ask_analyst",
                                              "arguments": {"question": "How many orders were placed?"}}, ident=3)
    result = called["result"]
    assert not result.get("isError")
    answer = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert answer["answer"] == "There were 61,948 orders." and answer["checked"]
    _, metrics = rpc(app, auth, "tools/call", {"name": "list_metrics", "arguments": {}}, ident=4)
    assert "revenue_pre_tax" in json.dumps(metrics["result"])
