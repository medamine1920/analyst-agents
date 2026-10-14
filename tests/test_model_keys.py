"""Users' own model keys: checked with the provider, stored encrypted, used for their questions only,
and kept out of the shared budget. The provider is faked; no test reaches a model."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents import providers
from app.agents.orchestrated.plan import Plan
from app.api.main import create_app
from app.api.model_keys import ModelKeyStore
from app.api.security import Limits, SecurityStore
from app.connectors import http
from app.connectors.http import ConnectorError
from app.mcp_server.server import server

PASSWORD = "correct horse battery"
CLAUDE_KEY = "sk-ant-test-0123456789abcdef"
PLAN = Plan.model_validate({"question_type": "lookup", "steps": [
    {"id": "orders", "purpose": "Count all orders", "metrics": ["order_count"]}]})


def provider_api(request: httpx.Request) -> httpx.Response:
    """A fake of the four providers' 'list models' endpoints. Only CLAUDE_KEY (and 'good' keys) are accepted."""
    sent = request.headers.get("x-api-key") or request.headers.get("x-goog-api-key") or \
        request.headers.get("authorization", "").removeprefix("Bearer ")
    assert "key" not in str(request.url.params), "a key must never travel in the address"
    if sent != CLAUDE_KEY and not sent.startswith("good"):
        return httpx.Response(401, json={"error": "invalid key"})
    host = request.url.host
    if host == "api.anthropic.com":
        return httpx.Response(200, json={"data": [{"id": "claude-opus-5-5"}, {"id": "claude-haiku-5-5"}]})
    if host == "generativelanguage.googleapis.com":
        return httpx.Response(200, json={"models": [
            {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]}]})
    return httpx.Response(200, json={"data": [{"id": "gpt-5"}, {"id": "whisper-1"}, {"id": "text-embedding-3-large"},
                                              {"id": "gpt-4o-realtime-preview"}]})


@pytest.fixture(autouse=True)
def fake_providers(monkeypatch):
    monkeypatch.setattr(http, "check_host", lambda host: None)
    http.TRANSPORT = httpx.MockTransport(provider_api)
    yield
    http.TRANSPORT = None


def test_a_key_is_checked_by_listing_the_providers_chat_models():
    assert asyncio.run(providers.check_key("anthropic", CLAUDE_KEY)) == ["claude-opus-5-5", "claude-haiku-5-5"]
    assert asyncio.run(providers.check_key("openai", "good-openai")) == ["gpt-5"]  # no audio, embeddings, realtime
    assert asyncio.run(providers.check_key("google", "good-gemini")) == ["gemini-2.5-pro"]
    with pytest.raises(ConnectorError, match="refused the credentials"):
        asyncio.run(providers.check_key("openai", "sk-wrong-key-123"))


def test_keys_are_encrypted_at_rest_and_only_a_hint_is_returned(tmp_path):
    store = ModelKeyStore(tmp_path / "console.sqlite")
    saved = store.save("ana", "anthropic", CLAUDE_KEY, "claude-opus-5-5", ["claude-opus-5-5", "claude-haiku-5-5"])
    assert CLAUDE_KEY not in json.dumps(saved) and saved["active"]
    assert CLAUDE_KEY.encode() not in (tmp_path / "console.sqlite").read_bytes()
    assert store.active("ana")["key"] == CLAUDE_KEY and store.active("bob") is None
    with pytest.raises(ValueError):
        store.choose("ana", "anthropic", "gpt-5")  # not a model this key can use
    store.choose("ana", "anthropic", "claude-haiku-5-5")
    assert store.active("ana")["model"] == "claude-haiku-5-5"
    store.choose("ana", None)  # back to the console's model
    assert store.active("ana") is None
    assert store.remove("ana", "anthropic") and store.list("ana") == []


@pytest.fixture
def console(tmp_path):
    security = SecurityStore(tmp_path / "console.sqlite")
    security.add_user("ana", PASSWORD)
    security.add_user("bob", PASSWORD)
    built = []

    async def server_plan(messages):
        return PLAN.model_copy(deep=True), None, {"input_tokens": 3000, "output_tokens": 200}

    async def server_write(messages):
        return "There were 61,948 orders.", {"input_tokens": 1000, "output_tokens": 50}

    def factory(provider, model, key):
        built.append((provider, model, key))
        return server_plan, server_write

    app = create_app(mcp_target=server, plan_fn=server_plan, write_fn=server_write, traces_dir=tmp_path / "traces",
                     checkpoint_db=tmp_path / "c.sqlite", results_dir=tmp_path, auth=True,
                     security_db=tmp_path / "console.sqlite", limits=Limits(daily_tokens=4000),
                     model_factory=factory)
    with TestClient(app) as client:
        yield client, built


def sign_in(client, name):
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"name": name, "password": PASSWORD}).status_code == 200


def ask(client, question):
    response = client.post("/api/ask", json={"question": question, "fresh": True})
    return response.status_code, [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.mark.integration
def test_a_users_own_key_runs_their_questions_outside_the_shared_budget(console):
    client, built = console
    sign_in(client, "ana")
    wrong = client.post("/api/models/keys", json={"provider": "anthropic", "key": "sk-ant-wrong-000000"})
    assert wrong.status_code == 422 and "not accepted" in wrong.json()["detail"]

    added = client.post("/api/models/keys", json={"provider": "anthropic", "key": CLAUDE_KEY}).json()
    assert added["model"] == "claude-opus-5-5" and added["active"] and CLAUDE_KEY not in json.dumps(added)
    listed = client.get("/api/models").json()
    assert CLAUDE_KEY not in json.dumps(listed) and listed["current"]["own"]
    assert client.get("/api/auth/me").json()["model"]["model"] == "claude-opus-5-5"

    # The console's own budget (4,000 tokens) is spent by the first question on it...
    sign_in(client, "bob")
    status, _ = ask(client, "How many orders were there?")
    assert status == 200
    assert ask(client, "How many orders in all?")[0] == 429
    assert built == []  # bob has no key: the console's model answered

    # ...but ana's questions run on her key and are not stopped by it.
    sign_in(client, "ana")
    status, events = ask(client, "How many orders were there in total?")
    assert status == 200 and events[-1]["type"] == "saved"
    assert built == [("anthropic", "claude-opus-5-5", CLAUDE_KEY)]
    assert client.get("/api/history").json()[0]["model"] == "anthropic:claude-opus-5-5"

    # Back to the console's model: the shared budget applies again.
    assert client.put("/api/models/active", json={"provider": None}).json()["current"]["own"] is False
    assert ask(client, "How many orders, all told?")[0] == 429

    # Another user never sees ana's keys; an app key (ak_...) cannot manage model keys.
    sign_in(client, "bob")
    assert client.get("/api/models").json()["keys"] == []
    assert client.delete("/api/models/keys/anthropic").status_code == 404
    sign_in(client, "ana")
    app_key = client.post("/api/keys", json={"name": "script"}).json()["key"]
    client.cookies.clear()
    assert client.get("/api/models", headers={"Authorization": f"Bearer {app_key}"}).status_code == 403


def test_the_planner_moves_to_the_next_way_of_asking_when_the_provider_refuses_one():
    """Claude refuses forced tool calls and older models refuse structured output: try the next way once."""
    from app.agents.orchestrated.graph import make_plan_fn

    class Model:
        def __init__(self):
            self.asked = []

        def with_structured_output(self, schema, method, include_raw):
            model = self

            class Runner:
                async def ainvoke(self, messages):
                    model.asked.append(method)
                    if method == "json_schema":
                        raise RuntimeError("400: output_config.format is not supported on this model")
                    return {"parsed": PLAN, "raw": None, "parsing_error": None}

            return Runner()

    model = Model()
    plan_fn = make_plan_fn(model, ("json_schema", "function_calling"))
    assert asyncio.run(plan_fn([]))[1].startswith("Switching to function_calling")
    assert asyncio.run(plan_fn([]))[0] == PLAN
    assert model.asked == ["json_schema", "function_calling"]
