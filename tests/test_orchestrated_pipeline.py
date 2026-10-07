"""Integration tests: the orchestrated graph against the real MCP server, with scripted model outputs.

If the planner plans well, code must produce the golden numbers; when the planner or writer
makes a mistake, the graph must catch it and give it a bounded second chance.
"""

import pytest
from mcp import Client

from app.agents.orchestrated.gateway import McpGateway
from app.agents.orchestrated.graph import build_graph
from app.agents.orchestrated.plan import Plan
from app.agents.orchestrated.review import supported_numbers
from app.mcp_server.server import server
from evals.scoring import score

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def scripted(*outputs):
    queue = list(outputs)

    async def fn(messages):
        return queue.pop(0)

    return fn


async def run(question, plans, drafts=("draft",)):
    plan_outputs = [(Plan.model_validate(p), None, {}) if isinstance(p, dict) else p for p in plans]
    async with Client(server) as client:
        graph = build_graph(McpGateway(client), scripted(*plan_outputs), scripted(*[(d, {}) for d in drafts]))
        return await graph.ainvoke({"question": question})


def has(facts, *numbers):
    found = supported_numbers(facts)
    return all(any(abs(f - n) <= 0.006 for f in found) for n in numbers)


MARCH = {"question_type": "change_explanation", "steps": [
    {"id": "feb", "purpose": "Feb", "metrics": ["revenue_pre_tax"], "start_date": "2025-02-01", "end_date": "2025-02-28"},
    {"id": "mar", "purpose": "Mar", "metrics": ["revenue_pre_tax"], "start_date": "2025-03-01", "end_date": "2025-03-31"}],
    "comparisons": [{"before_step": "feb", "after_step": "mar"}]}


async def test_change_questions_get_a_store_breakdown_automatically():
    state = await run("Pre-tax revenue jumped in March 2025 compared with February 2025. Why?", [MARCH])
    assert {s["id"] for s in state["facts"]["steps"]} >= {"feb_by_store", "mar_by_store"}
    assert has(state["facts"], 26983, 19610, 72.68, 7373)
    guide = state["facts"]["computed_fields"]
    assert "share_of_total_change_pct" in guide and "same period" in guide["<metric>_share_pct"]


async def test_shares_and_formulas_are_computed_in_code():
    shares = await run("How did the beverage share of item revenue change between January and August 2025?", [{
        "question_type": "share", "steps": [
            {"id": "jan", "purpose": "", "metrics": ["item_revenue"], "group_by": ["product__product_type"],
             "start_date": "2025-01-01", "end_date": "2025-01-31"},
            {"id": "aug", "purpose": "", "metrics": ["item_revenue"], "group_by": ["product__product_type"],
             "start_date": "2025-08-01", "end_date": "2025-08-31"}]}])
    assert has(shares["facts"], 58.79, 66.81)
    tax = await run("Give the effective tax rate for each store.", [{
        "question_type": "comparison",
        "steps": [{"id": "s", "purpose": "", "metrics": ["tax_collected", "revenue_pre_tax"],
                   "group_by": ["store__store_name"]}],
        "calculations": [{"name": "rate", "step_id": "s", "expression": "tax_collected / revenue_pre_tax"}]}])
    assert has(tax["facts"], 0.04, 0.0599)


async def test_ambiguous_term_adds_every_definition_and_review_enforces_it():
    plan = {"question_type": "ranking", "steps": [
        {"id": "s", "purpose": "", "metrics": ["revenue_pre_tax"], "group_by": ["store__store_name"]}]}
    one_definition = "Philadelphia had the higher income: 425,467 (Revenue (pre-tax))."
    both = "Philadelphia: Revenue (pre-tax) 425,467; Revenue (with tax) 450,969.65."
    state = await run("Which store had the higher income?", [plan], drafts=(one_definition, both))
    assert has(state["facts"], 425467, 450969.65)
    assert state["write_attempts"] == 2 and state["answer"] == both


async def test_unrelated_use_of_an_ambiguous_word_is_not_enforced():
    plan = {"question_type": "listing", "steps": [
        {"id": "s", "purpose": "", "metrics": ["store_count", "order_count"], "group_by": ["store__store_name"]}]}
    answer = "We have 6 stores. Chicago, Los Angeles, New Orleans and San Francisco had no sales."
    state = await run("How many stores do we have, and which of them had no sales?", [plan], drafts=(answer,))
    assert state["ambiguities"] == [] and state["write_attempts"] == 1
    checks = [{"number": 6}, {"text_any": ["chicago"]}, {"text_any": ["los angeles"]},
              {"text_any": ["new orleans"]}, {"text_any": ["san francisco"]}]
    assert score(state["answer"], checks)[0]


async def test_invalid_plan_gets_one_more_try_with_the_errors():
    bad = {"question_type": "lookup", "steps": [{"id": "s", "purpose": "", "metrics": ["revenue"]}]}
    good = {"question_type": "lookup", "steps": [{"id": "s", "purpose": "", "metrics": ["order_count"]}]}
    state = await run("How many orders were placed?", [bad, good], drafts=("There were 61,948 orders.",))
    assert state["plan_attempts"] == 2 and state["answer"] == "There were 61,948 orders."


async def test_made_up_numbers_trigger_a_rewrite():
    plan = {"question_type": "lookup", "steps": [{"id": "s", "purpose": "", "metrics": ["order_count"]}]}
    state = await run("How many orders were placed?", [plan],
                      drafts=("There were 62,500 orders.", "There were 61,948 orders."))
    assert state["write_attempts"] == 2 and state["answer"] == "There were 61,948 orders."


async def test_planner_that_never_succeeds_gets_an_honest_answer():
    failure = (None, "not JSON", {})
    state = await run("How many orders?", [failure, failure, failure])
    assert state["answer"].startswith("I could not build a valid analysis plan")


async def test_store_count_survives_the_full_data_range_and_missing_stores_are_listed():
    """Reproduces eval failure a02: the planner dated the store count, which erased every store."""
    period = {"start_date": "2024-09-01", "end_date": "2025-08-31"}
    plan = {"question_type": "listing", "steps": [
        {"id": "stores_total", "purpose": "", "metrics": ["store_count"], "group_by": ["store__store_name"], **period},
        {"id": "stores_sales", "purpose": "", "metrics": ["order_count"], "group_by": ["store__store_name"], **period}]}
    state = await run("How many stores do we have, and which of them had no sales?", [plan])
    totals = {s["id"]: s for s in state["facts"]["steps"]}
    assert len(totals["stores_total"]["rows"]) == 6
    missing = state["facts"]["groups_missing_between_steps"][0]
    assert missing["groups"] == ["Chicago", "Los Angeles", "New Orleans", "San Francisco"]


async def test_breakdowns_carry_period_totals():
    """Reproduces eval failure m06: the month total (283) existed only as 255 + 28."""
    plan = {"question_type": "ranking", "steps": [
        {"id": "s", "purpose": "", "metrics": ["new_customers"], "group_by": ["metric_time__month", "store__store_name"]}]}
    state = await run("Which month had the most new customers, and which store did most come from?", [plan])
    step = state["facts"]["steps"][0]
    march = next(t for t in step["totals"] if str(t["metric_time__month"]).startswith("2025-03"))
    assert march["new_customers_total"] == 283


async def test_breakdowns_carry_the_gap_to_the_top_group():
    """Reproduces held-out failure h06: 'which store is higher, and by how much?'"""
    plan = {"question_type": "comparison", "steps": [
        {"id": "s", "purpose": "", "metrics": ["average_order_value"], "group_by": ["store__store_name"],
         "start_date": "2025-01-01", "end_date": "2025-08-31"}]}
    state = await run("Which store had the higher average order value in 2025, and by how much?", [plan])
    rows = {r["store__store_name"]: r for r in state["facts"]["steps"][0]["rows"]}
    assert rows["Philadelphia"]["average_order_value_gap_to_top"] == 0
    assert rows["Brooklyn"]["average_order_value_gap_to_top"] == pytest.approx(0.98, abs=0.005)


async def test_a_rejected_formula_gets_another_planning_attempt():
    bad = {"question_type": "comparison", "steps": [
        {"id": "a", "purpose": "", "metrics": ["order_count"], "start_date": "2025-01-01", "end_date": "2025-03-31"}],
        "calculations": [{"name": "x", "step_id": "a", "expression": "a.order_count * 2"}]}
    good = {"question_type": "lookup", "steps": [
        {"id": "a", "purpose": "", "metrics": ["order_count"], "start_date": "2025-01-01", "end_date": "2025-03-31"}]}
    state = await run("How many orders in Q1 2025?", [bad, good], drafts=("There were 13,076 orders.",))
    assert state["plan_attempts"] == 2
