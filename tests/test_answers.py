"""Saved answers (no tokens for a repeated question) and suggestions."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.agents.orchestrated.plan import Plan
from app.api.answers import AnswerStore, normalized_question, reusable, suggest
from app.api.main import create_app
from app.mcp_server.server import server

DONE = [{"type": "start"}, {"type": "step", "node": "review", "data": {"issues": []}},
        {"type": "done", "answer": "There were 61,948 orders.", "usage": {"input_tokens": 10}}]


def test_the_same_question_in_other_words_is_not_the_same_but_spacing_and_case_are():
    assert normalized_question("  How many ORDERS   were placed? ") == normalized_question("how many orders were placed")
    assert normalized_question("How many orders?") != normalized_question("How many customers?")


def test_only_checked_answers_without_a_choice_are_reused():
    assert reusable(DONE)
    failed_check = [DONE[0], {"type": "step", "node": "review", "data": {"issues": ["bad number"]}}, DONE[2]]
    picked = [DONE[0], {"type": "step", "node": "clarify", "data": {}}, *DONE[1:]]
    for events in (failed_check, picked, DONE[:2]):
        assert not reusable(events)


def test_saved_answers_expire_and_follow_the_data(tmp_path):
    answers = AnswerStore(tmp_path / "a.sqlite", max_age_days=7)
    assert answers.save("How many orders were placed?", False, "v1", DONE, "trace-1")
    assert answers.find("how many orders were placed", False, "v1")["trace_id"] == "trace-1"
    assert answers.find("How many orders were placed?", True, "v1") is None  # another mode
    assert answers.find("How many orders were placed?", False, "v2") is None  # the data changed
    old = AnswerStore(tmp_path / "a.sqlite", max_age_days=0)
    time.sleep(0.01)
    assert old.find("How many orders were placed?", False, "v1") is None  # too old


def test_suggestions_rank_past_questions_and_match_metrics():
    past = ["Which store had the higher income?", "How many orders in March?", "which store had the higher income"]
    catalog = {"order_count": {"label": "Orders", "description": "Number of orders placed."},
               "store_count": {"label": "Stores", "description": "Number of stores."}}
    found = suggest("store", past, catalog)
    assert found["questions"] == [{"question": "Which store had the higher income?", "times_asked": 2}]
    assert [m["name"] for m in found["metrics"]] == ["store_count"]
    assert suggest("", past, catalog)["metrics"] == []
    # A whole question still suggests the metric it names, even though most of its words name none.
    assert [m["name"] for m in suggest("how many orders did we get in march", [], catalog)["metrics"]] == ["order_count"]


# ---- through the API ------------------------------------------------------------------------

def events_of(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def counted(tmp_path):
    calls = {"plan": 0, "write": 0}
    plan = Plan.model_validate({"question_type": "lookup", "steps": [
        {"id": "orders", "purpose": "Count all orders", "metrics": ["order_count"]}]})

    async def plan_fn(messages):
        calls["plan"] += 1
        return plan.model_copy(deep=True), None, {"input_tokens": 3000, "output_tokens": 200}

    async def write_fn(messages):
        calls["write"] += 1
        return "There were 61,948 orders.", {"input_tokens": 1000, "output_tokens": 50}

    app = create_app(mcp_target=server, plan_fn=plan_fn, write_fn=write_fn, traces_dir=tmp_path / "traces",
                     checkpoint_db=tmp_path / "c.sqlite", results_dir=tmp_path, auth=False,
                     security_db=tmp_path / "console.sqlite")
    with TestClient(app) as client:
        yield client, calls


@pytest.mark.integration
def test_a_repeated_question_is_answered_from_the_saved_run(counted):
    client, calls = counted
    first = events_of(client.post("/api/ask", json={"question": "How many orders were placed?"}))
    assert first[-1]["type"] == "saved" and calls == {"plan": 1, "write": 1}

    again = events_of(client.post("/api/ask", json={"question": "how many orders were placed"}))
    assert calls == {"plan": 1, "write": 1}  # no model call
    reused = next(e for e in again if e["type"] == "reused")
    assert reused["trace_id"] == first[-1]["id"]
    done = next(e for e in again if e["type"] == "done")
    assert done["reused"] and done["answer"] == "There were 61,948 orders."
    assert client.get("/api/auth/me").json()["usage"]["tokens_today"] == 4250  # the replay cost nothing

    events_of(client.post("/api/ask", json={"question": "How many orders were placed?", "fresh": True}))
    assert calls == {"plan": 2, "write": 2}  # "ask again" runs the agent

    found = client.get("/api/suggestions", params={"q": "orders placed"}).json()
    assert found["questions"][0]["times_asked"] == 3 and found["questions"][0]["saved_answer"]
    assert "order_count" in [m["name"] for m in found["metrics"]]
