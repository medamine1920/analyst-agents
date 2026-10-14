"""Integration tests: the orchestrated graph against the real MCP server, with scripted model outputs.

If the planner plans well, code must produce the golden numbers; when the planner or writer
makes a mistake, the graph must catch it and give it a bounded second chance.
"""

from contextlib import asynccontextmanager

import pytest
from langgraph.types import Command
from mcp import Client

from app.agents.orchestrated.gateway import McpGateway
from app.agents.orchestrated.graph import (
    build_graph,
    open_checkpointer,
    pending_approval,
    thread_config,
)
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


# ---- Human approval: pause on an ambiguous term, resume with the chosen definition ----------

INCOME_PLAN = {"question_type": "ranking", "steps": [
    {"id": "s", "purpose": "", "metrics": ["revenue_pre_tax"], "group_by": ["store__store_name"]}]}
INCOME_QUESTION = "Which store had the higher income?"


@asynccontextmanager
async def durable_graph(database, plans=(), drafts=()):
    """A graph whose pauses are stored in a SQLite file, like the console and the command line."""
    plan_outputs = [(Plan.model_validate(p), None, {}) for p in plans]
    async with Client(server) as client, open_checkpointer(database) as checkpointer:
        yield build_graph(McpGateway(client), scripted(*plan_outputs), scripted(*[(d, {}) for d in drafts]),
                          checkpointer)


async def test_an_ambiguous_question_pauses_survives_a_restart_and_resumes_with_the_choice(tmp_path):
    database, config = tmp_path / "checkpoints.sqlite", thread_config("thread-1")
    async with durable_graph(database, plans=[INCOME_PLAN]) as graph:
        paused = await graph.ainvoke({"question": INCOME_QUESTION, "interactive": True}, config)
        assert "answer" not in paused and "results" not in paused  # nothing was queried before the user answered

    # Everything above is gone (connection, graph, MCP client), as after a process restart. The new
    # planner has no scripted output: resuming must not call the model again.
    with_tax = "Philadelphia had the higher income: 450,969.65, measured as Revenue (with tax)."
    async with durable_graph(database, drafts=[with_tax]) as graph:
        request = pending_approval(await graph.aget_state(config))
        question = request["questions"][0]
        assert question["term"] == "income"
        assert [o["metric"] for o in question["options"]] == ["revenue_pre_tax", "revenue_with_tax"]
        assert all(o["label"] and o["description"] for o in question["options"])
        state = await graph.ainvoke(Command(resume={"income": "revenue_with_tax"}), config)

    assert state["choices"] == {"income": "revenue_with_tax"} and state["ambiguities"] == []
    assert state["plan"]["steps"][0]["metrics"] == ["revenue_with_tax"]
    assert has(state["facts"], 450969.65) and not has(state["facts"], 425467)
    assert state["answer"] == with_tax and state["write_attempts"] == 1 and state["plan_attempts"] == 1


async def test_answering_all_reports_every_definition_as_before(tmp_path):
    both = "Philadelphia: Revenue (pre-tax) 425,467; Revenue (with tax) 450,969.65."
    async with durable_graph(tmp_path / "c.sqlite", plans=[INCOME_PLAN], drafts=[both]) as graph:
        config = thread_config("thread-2")
        await graph.ainvoke({"question": INCOME_QUESTION, "interactive": True}, config)
        state = await graph.ainvoke(Command(resume="all"), config)
    assert metrics(state["ambiguities"]) == [["revenue_pre_tax", "revenue_with_tax"]]
    assert has(state["facts"], 425467, 450969.65) and state["answer"] == both


async def test_only_interactive_runs_with_an_active_ambiguity_pause(tmp_path):
    both = "Philadelphia: Revenue (pre-tax) 425,467; Revenue (with tax) 450,969.65."
    orders = {"question_type": "lookup", "steps": [{"id": "s", "purpose": "", "metrics": ["order_count"]}]}
    async with durable_graph(tmp_path / "c.sqlite", plans=[INCOME_PLAN, orders],
                             drafts=[both, "There were 61,948 orders."]) as graph:
        # The default (what the evals run): no pause, every definition reported.
        state = await graph.ainvoke({"question": INCOME_QUESTION}, thread_config("a"))
        assert state["answer"] == both and pending_approval(await graph.aget_state(thread_config("a"))) is None
        # Interactive, but nothing to ask.
        state = await graph.ainvoke({"question": "How many orders were placed?", "interactive": True},
                                    thread_config("b"))
        assert state["answer"] == "There were 61,948 orders."


def metrics(items):
    return [item["metrics"] for item in items]


async def test_the_top_period_is_broken_down_even_when_the_plan_splits_it_wrongly():
    """Reproduces the m06 spot-check failure of 2026-10-09: the planner asked for new customers by month
    and, separately, by store over all dates; the answer then gave Brooklyn's all-time 493 as March's."""
    plan = {"question_type": "ranking", "steps": [
        {"id": "by_month", "purpose": "", "metrics": ["new_customers"], "group_by": ["metric_time__month"]},
        {"id": "by_store", "purpose": "", "metrics": ["new_customers"], "group_by": ["store__store_name"]}]}
    state = await run("Which month had the most new customers, how many were there, and which store did most "
                      "of them come from?", [plan])
    added = next(s for s in state["facts"]["steps"] if s["id"] == "by_month_by_store")
    top = added["top_period"][0]
    assert top["metric"] == "new_customers" and str(top["metric_time__month"]).startswith("2025-03")
    assert top["total"] == 283
    assert {r["store__store_name"]: r["new_customers"] for r in top["rows"]} == {"Brooklyn": 255, "Philadelphia": 28}
    assert "top_period" in state["facts"]["computed_fields"]
    by_store = next(s for s in state["facts"]["steps"] if s["id"] == "by_store")
    assert by_store["period"] == "all dates in the data"  # so 493 cannot pass for one month's number


async def test_a_replan_after_a_failed_query_reuses_the_choice_without_asking_again(tmp_path):
    """The user is asked once per term: when the chosen plan's query fails and the planner tries again,
    the second plan must get the same definition, with no second pause."""

    class FailsOnce(McpGateway):
        failed = False

        async def run_step(self, step):
            if not FailsOnce.failed:
                FailsOnce.failed = True
                raise ValueError("the warehouse timed out")
            return await super().run_step(step)

    with_tax = "Philadelphia had the higher income: 450,969.65, measured as Revenue (with tax)."
    plans = [(Plan.model_validate(INCOME_PLAN), None, {}), (Plan.model_validate(INCOME_PLAN), None, {})]
    async with Client(server) as client, open_checkpointer(tmp_path / "c.sqlite") as checkpointer:
        graph = build_graph(FailsOnce(client), scripted(*plans), scripted((with_tax, {})), checkpointer)
        config = thread_config("replan")
        await graph.ainvoke({"question": INCOME_QUESTION, "interactive": True}, config)
        state = await graph.ainvoke(Command(resume={"income": "revenue_with_tax"}), config)
        assert pending_approval(await graph.aget_state(config)) is None  # not asked a second time

    assert FailsOnce.failed and state["plan_attempts"] == 2
    assert state["plan"]["steps"][0]["metrics"] == ["revenue_with_tax"]
    assert has(state["facts"], 450969.65) and not has(state["facts"], 425467)
    assert state["answer"] == with_tax
