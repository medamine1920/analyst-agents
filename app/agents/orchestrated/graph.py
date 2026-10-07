"""Orchestrated analyst: plan (model) -> execute (code) -> analyze (code) -> write (model) -> review (code).

    python -m app.agents.orchestrated.graph "Why did revenue jump in March 2025?"
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.agents.orchestrated.analysis import build_facts
from app.agents.orchestrated.gateway import McpGateway
from app.agents.orchestrated.glossary import resolve_terms
from app.agents.orchestrated.plan import (
    Plan,
    apply_ambiguity,
    ensure_decomposition,
    normalize_dates,
    validate_plan,
)
from app.agents.orchestrated.review import review_answer

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL = "openai/gpt-oss-120b"
MAX_PLAN_ATTEMPTS = 3
MAX_WRITE_ATTEMPTS = 2
MAX_FACT_ROWS = 60

# (messages) -> (parsed plan or None, error text or None, usage dict)
PlanFn = Callable[[list], Awaitable[tuple[Plan | None, str | None, dict]]]
# (messages) -> (answer text, usage dict)
WriteFn = Callable[[list], Awaitable[tuple[str, dict]]]

PLANNER_PROMPT = """\
You plan data analysis for a coffee-and-sandwich chain. You never compute numbers yourself:
you choose which governed metrics to query, how to break them down, over which dates, and
which formulas to apply. Code runs everything you plan.

Rules:
- Use only metric and group_by names from the catalog. Each metric lists its valid group_by
  names; time dimensions also accept __week, __month, __quarter, __year.
- Use start_date and end_date (inclusive ISO dates) for periods. The data covers {first} to
  {last}. Relative dates such as "last month" are relative to {last}.
- To compare two periods: one step per period with identical metrics, group_by and filters
  (no time group_by), plus a comparison from the earlier step to the later one.
- To explain a change: question_type "change_explanation" with a comparison of the two
  periods. Code adds store and order-volume breakdowns automatically.
- For a ratio between metrics (e.g. effective tax rate), add a calculation on the step.
- Shares of a total within a one-dimension breakdown are computed automatically.
- Filters use a group_by name and an exact value, e.g. store__store_name = Brooklyn.
- To find items with no activity (e.g. stores without sales), query store_count together
  with the activity metric grouped by the same dimension.
{notes}
Catalog:
{catalog}
"""

WRITER_PROMPT = """\
You write the final answer for a business user, using ONLY numbers that appear in the facts.
You may add thousands separators and round to 2 decimals, but never do new arithmetic:
differences, growth rates, contributions and shares are already computed (comparisons:
change, change_pct, share_of_total_change_pct; columns ending in _share_pct; calculation columns).
Answer the question directly first, then give the supporting numbers. Name the metrics used,
with their definitions exactly as listed under "definitions"; computed columns are explained
under "computed_fields". Describe what the numbers show and never state causes or events the
facts do not contain (for example that a store opened or a promotion ran); if a group had no
activity in an earlier period, say exactly that. Show money with at most 2 decimals.
Under 180 words, at most one small table.
{notes}"""


class AnalystState(TypedDict, total=False):
    question: str
    resolution: dict
    plan: dict
    feedback: list[str]
    plan_attempts: int
    results: dict
    facts: dict
    draft: str
    review_issues: list[str]
    write_attempts: int
    answer: str
    usage: list[dict]
    tool_calls: list[str]
    ambiguities: list[dict]
    error: str


def _catalog_text(catalog: dict) -> str:
    return "\n".join(
        f"- {name} ({info['label']}, {info['type']}): {info['description']} group_by: {', '.join(info['group_by'])}"
        for name, info in catalog.items()
    )


def _ambiguity_notes(resolution: dict) -> str:
    notes = []
    for item in resolution.get("ambiguities", []):
        notes.append(f"- '{item['term']}' is ambiguous: use ALL of {item['metrics']} wherever it is needed.")
    for item in resolution.get("resolved", []):
        notes.append(f"- '{item['term']}' means {item['metrics']} here.")
    return ("\nBusiness terms in this question:\n" + "\n".join(notes) + "\n") if notes else ""


def _trim(facts: dict) -> dict:
    for step in facts["steps"]:
        if len(step["rows"]) > MAX_FACT_ROWS:
            step["rows"] = step["rows"][:MAX_FACT_ROWS]
            step["truncated"] = True
    return facts


def build_graph(gateway: McpGateway, plan_fn: PlanFn, write_fn: WriteFn):
    async def resolve(state: AnalystState) -> dict:
        context = await gateway.context()
        return {"resolution": resolve_terms(state["question"]), "usage": [], "tool_calls": [],
                "plan_attempts": 0, "write_attempts": 0, "feedback": [],
                "facts": {"data_range": context["data_range"]}}

    async def plan(state: AnalystState) -> dict:
        context = await gateway.context()
        catalog, data_range = context["catalog"], context["data_range"]
        system = PLANNER_PROMPT.format(first=data_range["first_date"], last=data_range["last_date"],
                                       notes=_ambiguity_notes(state["resolution"]), catalog=_catalog_text(catalog))
        feedback, usage, attempts = list(state.get("feedback", [])), list(state["usage"]), state["plan_attempts"]
        while attempts < MAX_PLAN_ATTEMPTS:
            attempts += 1
            request = state["question"]
            if feedback:
                request += "\n\nYour previous plan had these problems; fix them:\n- " + "\n- ".join(feedback)
            parsed, error, call_usage = await plan_fn([SystemMessage(system), HumanMessage(request)])
            usage.append(call_usage)
            if parsed is None:
                feedback = [error or "The plan could not be parsed."]
                continue
            errors = validate_plan(parsed, set(catalog), gateway.cached_dimensions)
            if errors:
                feedback = errors
                continue
            used = {metric for step in parsed.steps for metric in step.metrics}
            # Enforce an ambiguity only if the plan measures that concept (e.g. "no sales" in a
            # question about stores without orders is not a request for revenue).
            active = [a for a in state["resolution"]["ambiguities"] if used & set(a["metrics"])]
            parsed = apply_ambiguity(parsed, active)
            parsed = normalize_dates(parsed, data_range["first_date"], data_range["last_date"])
            parsed = ensure_decomposition(parsed, gateway.cached_dimensions)
            return {"plan": parsed.model_dump(), "plan_attempts": attempts, "usage": usage, "feedback": [],
                    "ambiguities": active}
        return {"plan_attempts": attempts, "usage": usage, "error": "No valid plan: " + "; ".join(feedback)}

    async def execute(state: AnalystState) -> dict:
        plan_obj = Plan.model_validate(state["plan"])
        results, errors, calls = {}, [], list(state["tool_calls"])
        for step in plan_obj.steps:
            calls.append("query_metrics")
            try:
                results[step.id] = await gateway.run_step(step)
            except ValueError as exc:
                errors.append(f"Step {step.id} failed: {str(exc)[:600]}")
        return {"results": results, "feedback": errors, "tool_calls": calls}

    async def analyze(state: AnalystState) -> dict:
        context = await gateway.context()
        plan_obj = Plan.model_validate(state["plan"])
        facts = build_facts(plan_obj, state["results"], context["catalog"], context)
        return {"facts": _trim(facts)}

    async def write(state: AnalystState) -> dict:
        notes = ""
        for item in state.get("ambiguities", []):
            notes += (f"The term '{item['term']}' has several governed definitions ({', '.join(item['metrics'])}): "
                      "report the result for each and say which definition each number uses.\n")
        request = f"Question: {state['question']}\n\nFacts:\n{json.dumps(state['facts'], default=str)}"
        if state.get("review_issues"):
            request += (f"\n\nYour previous draft:\n{state['draft']}\n\nFix these problems:\n- "
                        + "\n- ".join(state["review_issues"]))
        text, call_usage = await write_fn([SystemMessage(WRITER_PROMPT.format(notes=notes)), HumanMessage(request)])
        return {"draft": text, "write_attempts": state["write_attempts"] + 1,
                "usage": [*state["usage"], call_usage]}

    async def review(state: AnalystState) -> dict:
        context = await gateway.context()
        issues = review_answer(state["draft"], state["facts"], state["question"],
                               state.get("ambiguities", []), context["catalog"])
        if issues and state["write_attempts"] < MAX_WRITE_ATTEMPTS:
            return {"review_issues": issues}
        return {"review_issues": issues, "answer": state["draft"]}

    async def give_up(state: AnalystState) -> dict:
        return {"answer": f"I could not build a valid analysis plan for this question. ({state.get('error', '')})"}

    def after_plan(state: AnalystState) -> str:
        return "give_up" if state.get("error") else "execute"

    def after_execute(state: AnalystState) -> str:
        if state["feedback"]:
            return "plan" if state["plan_attempts"] < MAX_PLAN_ATTEMPTS else "analyze_partial"
        return "analyze"

    async def analyze_partial(state: AnalystState) -> dict:
        """Out of planning attempts: drop the failed steps and analyze what did run."""
        plan_obj = Plan.model_validate(state["plan"])
        ok = set(state["results"])
        plan_obj.steps = [s for s in plan_obj.steps if s.id in ok]
        plan_obj.calculations = [c for c in plan_obj.calculations if c.step_id in ok]
        plan_obj.comparisons = [c for c in plan_obj.comparisons if c.before_step in ok and c.after_step in ok]
        return await analyze({**state, "plan": plan_obj.model_dump()})

    def after_review(state: AnalystState) -> str:
        return END if state.get("answer") else "write"

    graph = StateGraph(AnalystState)
    for name, node in [("resolve", resolve), ("plan", plan), ("execute", execute), ("analyze", analyze),
                       ("analyze_partial", analyze_partial), ("write", write), ("review", review),
                       ("give_up", give_up)]:
        graph.add_node(name, node)
    graph.add_edge(START, "resolve")
    graph.add_edge("resolve", "plan")
    graph.add_conditional_edges("plan", after_plan, ["execute", "give_up"])
    graph.add_conditional_edges("execute", after_execute, ["plan", "analyze", "analyze_partial"])
    graph.add_edge("analyze", "write")
    graph.add_edge("analyze_partial", "write")
    graph.add_edge("write", "review")
    graph.add_conditional_edges("review", after_review, ["write", END])
    graph.add_edge("give_up", END)
    return graph.compile()


# ---- Model-backed planner and writer (Groq) ---------------------------------------------

def groq_model():
    from langchain_groq import ChatGroq

    return ChatGroq(model=os.environ.get("GROQ_MODEL", DEFAULT_MODEL), temperature=0, max_retries=5)


def make_plan_fn(model) -> PlanFn:
    """Ask for the plan as JSON content (never as a tool call).

    First choice is json_schema (the provider enforces the shape). If the provider rejects the
    schema itself, switch once to json_mode with the schema written into the prompt.
    """
    modes = {
        "json_schema": model.with_structured_output(Plan, method="json_schema", include_raw=True),
        "json_mode": model.with_structured_output(Plan, method="json_mode", include_raw=True),
    }
    state = {"mode": "json_schema"}
    schema_note = SystemMessage("Respond with only a JSON object matching this JSON schema:\n"
                                + json.dumps(Plan.model_json_schema()))

    async def plan_fn(messages: list) -> tuple[Plan | None, str | None, dict]:
        request = messages if state["mode"] == "json_schema" else [*messages, schema_note]
        try:
            output: dict[str, Any] = await modes[state["mode"]].ainvoke(request)
        except Exception as exc:
            text = str(exc)
            if "429" in text or "rate limit" in text.lower():
                raise
            if state["mode"] == "json_schema" and ("response_format" in text or "schema" in text.lower()):
                state["mode"] = "json_mode"
                return None, "Switching to JSON mode; please produce the plan again.", {}
            return None, f"The plan was rejected: {text[:400]}", {}
        usage = getattr(output.get("raw"), "usage_metadata", None) or {}
        if output.get("parsing_error") is not None:
            return None, f"The plan did not match the schema: {str(output['parsing_error'])[:400]}", usage
        return output["parsed"], None, usage

    return plan_fn


def make_write_fn(model) -> WriteFn:
    async def write_fn(messages: list) -> tuple[str, dict]:
        response = await model.ainvoke(messages)
        return str(response.content), response.usage_metadata or {}

    return write_fn


async def main(question: str) -> None:
    from mcp import Client

    from app.agents.mcp_tools import default_target

    load_dotenv(REPO_ROOT / ".env")
    model = groq_model()
    async with Client(default_target()) as client:
        graph = build_graph(McpGateway(client), make_plan_fn(model), make_write_fn(model))
        state = await graph.ainvoke({"question": question})
    for step in state.get("plan", {}).get("steps", []):
        print(f"-> {step['id']}: {step['metrics']} by {step['group_by'] or '-'} "
              f"{step['start_date'] or ''}..{step['end_date'] or ''}  ({step['purpose']})")
    print("\n" + state["answer"])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit('usage: python -m app.agents.orchestrated.graph "<question>"')
    asyncio.run(main(sys.argv[1]))
