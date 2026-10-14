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


INCOME_PLAN = Plan.model_validate({"question_type": "ranking", "steps": [
    {"id": "s", "purpose": "Income by store", "metrics": ["revenue_pre_tax"], "group_by": ["store__store_name"]}]})
WITH_TAX = "Philadelphia had the higher income: 450,969.65, measured as Revenue (with tax)."


async def plan_fn(messages):
    plan = INCOME_PLAN if "income" in messages[-1].content else PLAN
    return plan.model_copy(deep=True), None, {"input_tokens": 3000, "output_tokens": 200}


async def write_fn(messages):
    answer = WITH_TAX if "income" in messages[-1].content else "There were 61,948 orders."
    return answer, {"input_tokens": 1000, "output_tokens": 50}


def events_of(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def client(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    record = {"set": "v2", "agent": "orchestrated", "model": "test-model", "id": "m01", "repeat": 0,
              "category": "multi_step", "passed": True, "failed_checks": [], "answer": "ok", "error": None,
              "latency_s": 1.0, "input_tokens": 10, "output_tokens": 5}
    (results / "v2_orchestrated_test.jsonl").write_text(json.dumps(record) + "\n")
    def start():
        return TestClient(create_app(mcp_target=server, plan_fn=plan_fn, write_fn=write_fn, results_dir=results,
                                     traces_dir=tmp_path / "traces", checkpoint_db=tmp_path / "checkpoints.sqlite",
                                     auth=False, security_db=tmp_path / "console.sqlite"))

    with start() as test_client:
        test_client.restart = start  # a second app on the same files, as after a server restart
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
    events = events_of(response)
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


def test_an_ambiguous_question_asks_for_approval_and_resumes_after_a_restart(client):
    question = "Which store had the higher income?"
    first = events_of(client.post("/api/ask", json={"question": question, "interactive": True}))
    assert [e["node"] for e in first if e["type"] == "step"] == ["resolve", "plan"]
    approval = first[-1]
    assert approval["type"] == "approval" and approval["questions"][0]["term"] == "income"
    assert client.get("/api/traces").json() == []  # an unfinished run is not a saved trace
    thread_id = approval["thread_id"]
    assert client.post("/api/resume", json={"thread_id": thread_id, "choice": "order_count"}).status_code == 422

    with client.restart() as restarted:
        rest = events_of(restarted.post("/api/resume", json={"thread_id": thread_id, "choice": "revenue_with_tax"}))
        assert [e["node"] for e in rest if e["type"] == "step"] == ["clarify", "execute", "analyze", "write", "review"]
        assert rest[0]["data"]["choices"] == {"income": "revenue_with_tax"}
        done = next(e for e in rest if e["type"] == "done")
        assert done["answer"] == WITH_TAX and done["usage"]["llm_calls"] == 2
        saved = next(e for e in rest if e["type"] == "saved")
        replay = restarted.get(f"/api/traces/{saved['id']}").json()
        assert replay["question"] == question
        assert [e["type"] for e in replay["events"]].count("step") == 7  # both halves, in one trace
        # Answered once: the same pause cannot be answered again.
        again = restarted.post("/api/resume", json={"thread_id": thread_id, "choice": "revenue_with_tax"})
        assert again.status_code == 404


def test_resume_rejects_unknown_runs(client):
    assert client.post("/api/resume", json={"thread_id": "0" * 32, "choice": "all"}).status_code == 404
    assert client.post("/api/resume", json={"thread_id": "../x", "choice": "all"}).status_code == 422


def test_the_scripted_console_answers_its_examples_and_says_so_for_other_questions(tmp_path, monkeypatch):
    from scripts import demo_console

    app = create_app(mcp_target=server, plan_fn=demo_console.plan_fn, write_fn=demo_console.write_fn,
                     traces_dir=tmp_path / "traces", checkpoint_db=tmp_path / "c.sqlite",
                     trace_label=demo_console.LABEL, auth=False, security_db=tmp_path / "console.sqlite")
    with TestClient(app) as scripted:
        first = events_of(scripted.post("/api/ask", json={"question": "Which store had the higher income?",
                                                          "interactive": True}))
        thread_id = first[-1]["thread_id"]
        rest = events_of(scripted.post("/api/resume", json={"thread_id": thread_id, "choice": "revenue_pre_tax"}))
        review = next(e for e in rest if e.get("node") == "review")
        assert review["data"]["issues"] == [] and "$425,467" in next(e for e in rest if e["type"] == "done")["answer"]
        assert scripted.get("/api/traces").json()[0]["label"] == demo_console.LABEL
        other = events_of(scripted.post("/api/ask", json={"question": "How many customers do we have?"}))
        assert "only answers its example questions" in next(e for e in other if e["type"] == "done")["answer"]


def test_live_runs_are_saved_apart_from_the_demo_runs_and_both_are_listed(tmp_path):
    demos, live = tmp_path / "demos", tmp_path / "live"
    demos.mkdir()
    (demos / "demo-1.json").write_text(json.dumps({"id": "demo-1", "question": "Demo?", "label": "Demo", "created": "1",
                                                   "events": []}))
    app = create_app(mcp_target=server, plan_fn=plan_fn, write_fn=write_fn, results_dir=tmp_path, traces_dir=demos,
                     live_traces_dir=live, checkpoint_db=tmp_path / "c.sqlite", auth=False,
                     security_db=tmp_path / "console.sqlite")
    with TestClient(app) as client:
        saved = next(e for e in events_of(client.post("/api/ask", json={"question": "How many orders were placed?"}))
                     if e["type"] == "saved")
        assert (live / f"{saved['id']}.json").exists() and not (demos / f"{saved['id']}.json").exists()
        assert {t["id"] for t in client.get("/api/traces").json()} == {"demo-1", saved["id"]}
        assert client.get("/api/traces/demo-1").json()["question"] == "Demo?"
        assert client.get(f"/api/traces/{saved['id']}").status_code == 200

