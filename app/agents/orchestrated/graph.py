"""Orchestrated analyst: plan (model) -> execute (code) -> analyze (code) -> write (model) -> review (code).

    python -m app.agents.orchestrated.graph "Why did revenue jump in March 2025?"
    python -m app.agents.orchestrated.graph --interactive "Which store had the higher income?"
    python -m app.agents.orchestrated.graph --resume <thread id>

With --interactive the agent pauses when a business term has several governed definitions and
asks which one is meant. The pause is saved to disk, so the answer can be given later, even
from another process. Without it (the default, and what the evals use) every definition is reported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agents.orchestrated.analysis import build_facts
from app.agents.orchestrated.gateway import McpGateway
from app.agents.orchestrated.glossary import resolve_terms
from app.agents.orchestrated.plan import (
    ALL_DEFINITIONS,
    Plan,
    ensure_decomposition,
    ensure_drilldown,
    normalize_dates,
    read_choices,
    repair_names,
    settle_ambiguities,
    validate_plan,
)
from app.agents.orchestrated.review import review_answer
from app.agents.sales_expert import expert as sales_expert

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL = "openai/gpt-oss-120b"
CHECKPOINT_DB = REPO_ROOT / "checkpoints" / "analyst.sqlite"
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


# What a user's own data source changes in the rules: the sample shop's examples go, the rest stays.
SOURCE_PROMPT_CHANGES = [
    ("a coffee-and-sandwich chain", "{business}"),
    ("  periods. Code adds store and order-volume breakdowns automatically.", "  periods."),
    ("(e.g. effective tax rate)", "(e.g. an average per unit)"),
    ("e.g. store__store_name = Brooklyn.", "e.g. sales__region = North."),
    (("- To find items with no activity (e.g. stores without sales), query store_count together\n"
      "  with the activity metric grouped by the same dimension.\n"),
     ("- For a change over time (month by month, week by week), add a step grouped by\n"
      "  metric_time__month (or __week, __day, __quarter, __year).\n")),
]


def source_prompts(business: str) -> tuple[str, str]:
    """Planner and writer rules for a data source a user added: same rules, none of the shop's examples."""
    planner = PLANNER_PROMPT
    for old, new in SOURCE_PROMPT_CHANGES:
        if old not in planner:
            raise AssertionError(f"The planner prompt changed; update SOURCE_PROMPT_CHANGES: {old!r}")
        planner = planner.replace(old, new.replace("{business}", business.replace("{", "(").replace("}", ")")))
    writer = WRITER_PROMPT.replace("that a store opened or a promotion ran", "that something new started or a campaign ran")
    return planner, writer


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
    # Human approval: ask which definition is meant instead of reporting them all.
    interactive: bool
    pending_ambiguities: list[dict]
    choices: dict[str, str]
    # The sales expert's notes for the console: definitions, a chart per result, follow-up questions.
    advice: dict
    # What the reviewer objected to at each attempt, so a failed answer can be traced back to its drafts.
    review_log: list[list[str]]


def _catalog_text(catalog: dict) -> str:
    return "\n".join(
        f"- {name} ({info['label']}, {info['type']}): {info['description']} group_by: {', '.join(info['group_by'])}"
        for name, info in catalog.items()
    )


def _ambiguity_notes(resolution: dict, choices: dict[str, str]) -> str:
    notes = []
    for item in resolution.get("ambiguities", []):
        chosen = choices.get(item["term"])
        if chosen in item["metrics"]:
            notes.append(f"- '{item['term']}' means ['{chosen}'] here (the user chose this definition).")
            continue
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


def approval_request(pending: list[dict], catalog: dict) -> dict:
    """What the user is asked: for each ambiguous term, its governed definitions in plain words."""
    return {"questions": [
        {"term": item["term"],
         "options": [{"metric": m, "label": catalog[m]["label"], "description": catalog[m]["description"]}
                     for m in item["metrics"]]}
        for item in pending]}


def build_graph(gateway: McpGateway, plan_fn: PlanFn, write_fn: WriteFn, checkpointer=None,
                use_glossary: bool = True, prompts: tuple[str, str] | None = None):
    """Build the analyst graph. Pausing for approval ({"interactive": True}) needs a checkpointer."""

    async def resolve(state: AnalystState) -> dict:
        context = await gateway.context()
        # The business glossary describes the sample shop's metrics; a user's own source has none yet.
        resolution = resolve_terms(state["question"]) if use_glossary else {"ambiguities": [], "resolved": []}
        return {"resolution": resolution, "usage": [], "tool_calls": [],
                "plan_attempts": 0, "write_attempts": 0, "feedback": [], "choices": {},
                "pending_ambiguities": [], "facts": {"data_range": context["data_range"]}}

    def complete(parsed: Plan, active: list[dict], choices: dict[str, str], data_range: dict) -> dict:
        """Settle the ambiguous terms, then add what good analysis always needs."""
        parsed, still_open = settle_ambiguities(parsed, active, choices)
        parsed = normalize_dates(parsed, data_range["first_date"], data_range["last_date"])
        parsed = ensure_decomposition(parsed, gateway.cached_dimensions)
        parsed = ensure_drilldown(parsed, gateway.cached_dimensions)
        return {"plan": parsed.model_dump(), "ambiguities": still_open, "pending_ambiguities": []}

    async def plan(state: AnalystState) -> dict:
        context = await gateway.context()
        catalog, data_range = context["catalog"], context["data_range"]
        choices = state.get("choices", {})
        # The sales expert adds what the question's sales words mean here (nothing if it uses none).
        vocabulary = sales_expert.planner_notes(state["question"], catalog)
        notes = _ambiguity_notes(state["resolution"], choices) + (f"\n{vocabulary}\n" if vocabulary else "")
        system = (prompts[0] if prompts else PLANNER_PROMPT).format(first=data_range["first_date"], last=data_range["last_date"],
                                       notes=notes, catalog=_catalog_text(catalog))
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
            parsed = repair_names(parsed)
            errors = validate_plan(parsed, set(catalog), gateway.cached_dimensions)
            if errors:
                feedback = errors
                continue
            used = {metric for step in parsed.steps for metric in step.metrics}
            # Enforce an ambiguity only if the plan measures that concept (e.g. "no sales" in a
            # question about stores without orders is not a request for revenue).
            active = [a for a in state["resolution"]["ambiguities"] if used & set(a["metrics"])]
            progress = {"plan_attempts": attempts, "usage": usage, "feedback": []}
            # Ask the user only once per term: a replan after a failed query reuses the earlier choice.
            pending = [a for a in active if a["term"] not in choices] if state.get("interactive") else []
            if pending:
                return {**progress, "plan": parsed.model_dump(), "ambiguities": active,
                        "pending_ambiguities": pending}
            return {**progress, **complete(parsed, active, choices, data_range)}
        return {"plan_attempts": attempts, "usage": usage, "error": "No valid plan: " + "; ".join(feedback)}

    async def clarify(state: AnalystState) -> dict:
        """Pause until the user says which definition they mean, then finish the plan in code.

        On resume this node runs again from its first line, so it must stay free of model calls
        and side effects: the plan it needs was already saved by the plan node.
        """
        context = await gateway.context()
        pending = state["pending_ambiguities"]
        answer = interrupt(approval_request(pending, context["catalog"]))
        choices = {**state.get("choices", {}), **read_choices(answer, pending)}
        update = complete(Plan.model_validate(state["plan"]), state["ambiguities"], choices, context["data_range"])
        return {**update, "choices": choices}

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

    async def advise(state: AnalystState) -> dict:
        """The sales expert (code, no model): definitions, charts and follow-ups for the console, and for the
        writer what each metric means and which way is good, so a fall in costs is not called a decline."""
        catalog = (await gateway.context())["catalog"]
        plan = state["plan"]
        advice = sales_expert.advise(state["question"], plan["question_type"], plan["steps"], state["facts"], catalog)
        meanings = sales_expert.writer_notes([m for s in plan["steps"] for m in s["metrics"]], catalog)
        return {"advice": advice, "facts": {**state["facts"], **({"meanings": meanings} if meanings else {})}}

    async def write(state: AnalystState) -> dict:
        notes = ""
        for term, chosen in state.get("choices", {}).items():
            if chosen != ALL_DEFINITIONS:
                notes += (f"The user was asked what '{term}' means and chose {chosen}: "
                          "say that the answer uses this definition.\n")
        for item in state.get("ambiguities", []):
            notes += (f"The term '{item['term']}' has several governed definitions ({', '.join(item['metrics'])}): "
                      "report the result for each and say which definition each number uses.\n")
        request = f"Question: {state['question']}\n\nFacts:\n{json.dumps(state['facts'], default=str)}"
        if state.get("review_issues"):
            request += (f"\n\nYour previous draft:\n{state['draft']}\n\nFix these problems:\n- "
                        + "\n- ".join(state["review_issues"]))
        text, call_usage = await write_fn([SystemMessage((prompts[1] if prompts else WRITER_PROMPT).format(notes=notes)), HumanMessage(request)])
        return {"draft": text, "write_attempts": state["write_attempts"] + 1,
                "usage": [*state["usage"], call_usage]}

    async def review(state: AnalystState) -> dict:
        context = await gateway.context()
        issues = review_answer(state["draft"], state["facts"], state["question"],
                               state.get("ambiguities", []), context["catalog"])
        log = [*state.get("review_log", []), issues]
        if issues and state["write_attempts"] < MAX_WRITE_ATTEMPTS:
            return {"review_issues": issues, "review_log": log}
        return {"review_issues": issues, "review_log": log, "answer": state["draft"]}

    async def give_up(state: AnalystState) -> dict:
        return {"answer": f"I could not build a valid analysis plan for this question. ({state.get('error', '')})"}

    def after_plan(state: AnalystState) -> str:
        if state.get("error"):
            return "give_up"
        return "clarify" if state.get("pending_ambiguities") else "execute"

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
    for name, node in [("resolve", resolve), ("plan", plan), ("clarify", clarify), ("execute", execute),
                       ("analyze", analyze), ("analyze_partial", analyze_partial), ("advise", advise),
                       ("write", write), ("review", review),
                       ("give_up", give_up)]:
        graph.add_node(name, node)
    graph.add_edge(START, "resolve")
    graph.add_edge("resolve", "plan")
    graph.add_conditional_edges("plan", after_plan, ["clarify", "execute", "give_up"])
    graph.add_edge("clarify", "execute")
    graph.add_conditional_edges("execute", after_execute, ["plan", "analyze", "analyze_partial"])
    graph.add_edge("analyze", "advise")
    graph.add_edge("analyze_partial", "advise")
    graph.add_edge("advise", "write")
    graph.add_edge("write", "review")
    graph.add_conditional_edges("review", after_review, ["write", END])
    graph.add_edge("give_up", END)
    return graph.compile(checkpointer=checkpointer)


# ---- Model-backed planner and writer (Groq) ---------------------------------------------

def groq_model():
    from langchain_groq import ChatGroq

    if not os.environ.get("GROQ_API_KEY"):  # also replaces a variable that is set but empty
        load_dotenv(REPO_ROOT / ".env", override=True)
    if not os.environ.get("GROQ_API_KEY"):
        raise RuntimeError(
            f"No Groq key found. Put GROQ_API_KEY=<your key> in {REPO_ROOT / '.env'} and restart the server, "
            "or run the console without a key: uvicorn scripts.demo_console:app --port 8001")
    return ChatGroq(model=os.environ.get("GROQ_MODEL", DEFAULT_MODEL), temperature=0, max_retries=5)


SWITCH_HINTS = ("response_format", "schema", "output_config", "output_format", "tool_choice", "not supported",
                "json_mode", "structured")


def make_plan_fn(model, modes: tuple[str, ...] = ("json_schema", "json_mode")) -> PlanFn:
    """Ask for the plan in the first way the provider accepts.

    Groq, OpenAI and Gemini: json_schema (the provider enforces the shape), then json_mode with the schema
    written into the prompt. Claude: json_schema, then a tool call (older models). If the provider rejects
    the way of asking itself, switch once to the next and ask again.
    """
    runners: dict[str, Any] = {}  # built when first needed: a fallback is often never used
    state = {"index": 0}
    schema_note = SystemMessage("Respond with only a JSON object matching this JSON schema:\n"
                                + json.dumps(Plan.model_json_schema()))

    async def plan_fn(messages: list) -> tuple[Plan | None, str | None, dict]:
        mode = modes[state["index"]]
        request = [*messages, schema_note] if mode == "json_mode" else messages
        try:
            if mode not in runners:
                runners[mode] = model.with_structured_output(Plan, method=mode, include_raw=True)
            output: dict[str, Any] = await runners[mode].ainvoke(request)
        except Exception as exc:
            text = str(exc)
            if "429" in text or "rate limit" in text.lower():
                raise
            if state["index"] + 1 < len(modes) and any(hint in text.lower() for hint in SWITCH_HINTS):
                state["index"] += 1
                return None, f"Switching to {modes[state['index']]}; please produce the plan again.", {}
            return None, f"The plan was rejected: {text[:400]}", {}
        usage = getattr(output.get("raw"), "usage_metadata", None) or {}
        if output.get("parsing_error") is not None:
            return None, f"The plan did not match the schema: {str(output['parsing_error'])[:400]}", usage
        return output["parsed"], None, usage

    return plan_fn


def make_write_fn(model) -> WriteFn:
    async def write_fn(messages: list) -> tuple[str, dict]:
        response = await model.ainvoke(messages)
        return response.text, response.usage_metadata or {}  # text only: Claude may add thinking blocks

    return write_fn


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def pending_approval(snapshot) -> dict | None:
    """The question a paused run is waiting on, or None if the run is not paused."""
    for task in getattr(snapshot, "tasks", ()):
        for item in task.interrupts:
            return item.value
    return None


def open_checkpointer(path: Path | None = None):
    """Durable storage for paused runs (a SQLite file; set CHECKPOINT_DB to move it)."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    path = Path(path or os.environ.get("CHECKPOINT_DB") or CHECKPOINT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    return AsyncSqliteSaver.from_conn_string(str(path))


def ask_on_terminal(request: dict) -> dict[str, str]:
    answers = {}
    for item in request["questions"]:
        print(f"\n'{item['term']}' has several definitions. Which one do you mean?")
        for number, option in enumerate(item["options"], start=1):
            print(f"  {number}. {option['label']} ({option['metric']}): {option['description']}")
        print(f"  {len(item['options']) + 1}. Report all of them")
        picked = input("Your choice (number): ").strip()
        index = int(picked) - 1 if picked.isdigit() else -1
        in_range = 0 <= index < len(item["options"])
        answers[item["term"]] = item["options"][index]["metric"] if in_range else ALL_DEFINITIONS
    return answers


def model_fns() -> tuple[PlanFn, WriteFn]:
    """The Groq-backed planner and writer for interactive use (command line, console).

    With LLM_CACHE=1 their answers are reused from disk; see cache.py. Evals build theirs directly
    with make_plan_fn / make_write_fn and are never cached.
    """
    from app.agents.orchestrated.cache import cache_dir, cached_plan_fn, cached_write_fn

    model = groq_model()
    plan_fn, write_fn = make_plan_fn(model), make_write_fn(model)
    directory = cache_dir()
    if directory:
        plan_fn = cached_plan_fn(plan_fn, model.model_name, directory)
        write_fn = cached_write_fn(write_fn, model.model_name, directory)
    return plan_fn, write_fn


async def main(question: str | None, interactive: bool = False, resume: str | None = None) -> None:
    from mcp import Client

    from app.agents.mcp_tools import default_target

    load_dotenv(REPO_ROOT / ".env")
    async with Client(default_target()) as client, open_checkpointer() as checkpointer:
        graph = build_graph(McpGateway(client), *model_fns(), checkpointer)
        config = thread_config(resume or uuid.uuid4().hex)
        if resume:
            state = (await graph.aget_state(config)).values
            if pending_approval(await graph.aget_state(config)) is None:
                print(f"No paused run with thread id {resume}.")
                return
        else:
            state = await graph.ainvoke({"question": question, "interactive": interactive}, config)
        while (request := pending_approval(await graph.aget_state(config))) is not None:
            print("Paused. To answer later:  python -m app.agents.orchestrated.graph "
                  f"--resume {config['configurable']['thread_id']}")
            state = await graph.ainvoke(Command(resume=ask_on_terminal(request)), config)
    for step in state.get("plan", {}).get("steps", []):
        print(f"-> {step['id']}: {step['metrics']} by {step['group_by'] or '-'} "
              f"{step['start_date'] or ''}..{step['end_date'] or ''}  ({step['purpose']})")
    print("\n" + state["answer"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="?")
    parser.add_argument("--interactive", action="store_true", help="ask which definition an ambiguous term means")
    parser.add_argument("--resume", metavar="THREAD_ID", help="answer a paused run")
    args = parser.parse_args()
    if not args.question and not args.resume:
        parser.error("give a question, or --resume <thread id>")
    sys.stdout.reconfigure(errors="replace")  # answers may hold characters a Windows console cannot print
    asyncio.run(main(args.question, args.interactive, args.resume))
