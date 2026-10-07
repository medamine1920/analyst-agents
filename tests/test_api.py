"""Integration tests for the Analyst Console API, with the real MCP server and a scripted model."""

import json

import pytest
from fastapi.testclient import TestClient

from app.agents.orchestrated.plan import Plan
from app.api.main import create_app
from app.mcp_server.server import server

pytestmark = pytest.mark.integration

PLAN = Plan.model_validate({"question_type": "lookup", "steps": [
    {"id": "orders", "purpose": "Count all orders", "metrics": ["order_count"]}]})


async def plan_fn(messages):
    return PLAN.model_copy(deep=True), None, {"input_tokens": 3000, "output_tokens": 200}


async def write_fn(messages):
    return "There were 61,948 orders.", {"input_tokens": 1000, "output_tokens": 50}


@pytest.fixture
def client(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    record = {"set": "v2", "agent": "orchestrated", "model": "test-model", "id": "m01", "repeat": 0,
              "category": "multi_step", "passed": True, "failed_checks": [], "answer": "ok", "error": None,
              "latency_s": 1.0, "input_tokens": 10, "output_tokens": 5}
    (results / "v2_orchestrated_test.jsonl").write_text(json.dumps(record) + "\n")
    app = create_app(mcp_target=server, plan_fn=plan_fn, write_fn=write_fn,
                     results_dir=results, traces_dir=tmp_path / "traces")
    with TestClient(app) as test_client:
        yield test_client


def test_metrics_lists_the_catalog_and_data_range(client):
    body = client.get("/api/metrics").json()
    assert body["data_range"] == {"first_date": "2024-09-01", "last_date": "2025-08-31"}
    names = {m["name"] for m in body["metrics"]}
    assert "revenue_pre_tax" in names and len(names) == 12
    assert any("store__store_name" in m["group_by"] for m in body["metrics"])


def test_evals_summarizes_each_results_file(client):
    runs = client.get("/api/evals").json()
    assert runs[0]["agent"] == "orchestrated" and runs[0]["summary"]["overall"]["pass_at_1"] == 1.0
    detail = client.get("/api/evals/v2_orchestrated_test.jsonl/m01").json()
    assert detail[0]["answer"] == "ok"


def test_unknown_files_are_rejected(client):
    assert client.get("/api/evals/../secret/m01").status_code == 404
    assert client.get("/api/traces/does-not-exist").status_code == 404


def test_ask_streams_every_step_then_saves_a_replayable_trace(client):
    response = client.post("/api/ask", json={"question": "How many orders were placed?"})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    nodes = [e["node"] for e in events if e["type"] == "step"]
    assert nodes == ["resolve", "plan", "execute", "analyze", "write", "review"]
    done = next(e for e in events if e["type"] == "done")
    assert done["answer"] == "There were 61,948 orders." and done["usage"]["input_tokens"] == 4000
    saved = next(e for e in events if e["type"] == "saved")
    replay = client.get(f"/api/traces/{saved['id']}").json()
    assert replay["question"] == "How many orders were placed?" and len(replay["events"]) == len(events) - 1
    assert client.get("/api/traces").json()[0]["id"] == saved["id"]


def test_short_questions_are_rejected(client):
    assert client.post("/api/ask", json={"question": "?"}).status_code == 422
