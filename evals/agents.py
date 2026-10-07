"""The agents the eval runner can measure, behind one interface: `async with open_agent(name) as ask`.

`ask(question)` returns the answer plus usage numbers, so both agents are scored identically.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from langchain_core.messages import AIMessage

from app.agents import baseline
from app.agents.mcp_tools import default_target, mcp_tools
from app.agents.orchestrated import graph as orchestrated
from app.agents.orchestrated.gateway import McpGateway
from app.agents.reliability import HARNESS_VERSION

Ask = Callable[[str], Awaitable[dict]]
AGENT_VERSIONS = {"baseline": HARNESS_VERSION, "orchestrated": "plan-execute-review-v1"}


def usage_totals(usages: list[dict]) -> dict:
    return {
        "llm_calls": len(usages),
        "input_tokens": sum(u.get("input_tokens", 0) for u in usages),
        "cached_tokens": sum((u.get("input_token_details") or {}).get("cache_read", 0) for u in usages),
        "output_tokens": sum(u.get("output_tokens", 0) for u in usages),
    }


@asynccontextmanager
async def open_baseline() -> AsyncIterator[Ask]:
    async with mcp_tools() as tools:
        agent = baseline.build_agent(baseline.build_model(), tools)

        async def ask(question: str) -> dict:
            messages = await baseline.run_question(agent, question)
            ai = [m for m in messages if isinstance(m, AIMessage)]
            return {
                "answer": baseline.final_answer(messages),
                "tool_calls": [call["name"] for m in ai for call in m.tool_calls],
                **usage_totals([m.usage_metadata or {} for m in ai]),
            }

        yield ask


@asynccontextmanager
async def open_orchestrated() -> AsyncIterator[Ask]:
    from mcp import Client

    model = orchestrated.groq_model()
    async with Client(default_target()) as client:
        graph = orchestrated.build_graph(
            McpGateway(client), orchestrated.make_plan_fn(model), orchestrated.make_write_fn(model)
        )

        async def ask(question: str) -> dict:
            state = await graph.ainvoke({"question": question})
            return {
                "answer": state["answer"],
                "tool_calls": state.get("tool_calls", []),
                "plan_attempts": state.get("plan_attempts"),
                "write_attempts": state.get("write_attempts"),
                **usage_totals(state.get("usage", [])),
            }

        yield ask


AGENTS = {"baseline": open_baseline, "orchestrated": open_orchestrated}
