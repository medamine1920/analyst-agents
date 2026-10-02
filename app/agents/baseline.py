"""Single-agent baseline: one ReAct loop over the semantic layer's MCP tools.

Ask one question from the command line:
    python -m app.agents.baseline "Which store had the higher pre-tax revenue?"
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agents.mcp_tools import mcp_tools

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "openai/gpt-oss-120b"
MAX_STEPS = 20

SYSTEM_PROMPT = """\
You are a data analyst for a coffee-and-sandwich chain. You answer questions using
governed business metrics available through your tools.

Rules:
- Every number in your answer must come from a tool result. Never estimate or invent figures.
- Find metrics with search_metrics, check valid group-bys with list_dimensions, then call query_metrics.
- The data does not run up to today. Call get_data_time_range before interpreting relative
  dates such as "last month" or "this year".
- If a business term matches more than one metric (for example "revenue"), report each
  matching metric with its value and definition instead of silently picking one.
- Give exact numbers as returned by the tools (you may add thousands separators),
  and name the metrics you used.
"""


def build_model() -> BaseChatModel:
    from langchain_groq import ChatGroq

    return ChatGroq(model=os.environ.get("GROQ_MODEL", DEFAULT_MODEL), temperature=0, max_retries=5)


def build_agent(model: BaseChatModel, tools: list[BaseTool]):
    """The classic ReAct loop as a two-node graph: the model thinks, tools act, repeat."""
    model_with_tools = model.bind_tools(tools)

    async def call_model(state: MessagesState) -> dict:
        response = await model_with_tools.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)  # tool calls -> "tools", else end
    graph.add_edge("tools", "agent")
    return graph.compile()


async def run_question(agent, question: str) -> list:
    """Run one question and return the full message history."""
    state = await agent.ainvoke(
        {"messages": [HumanMessage(question)]},
        config={"recursion_limit": MAX_STEPS},
    )
    return state["messages"]


def final_answer(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return str(message.content)
    return ""


async def main(question: str) -> None:
    load_dotenv(REPO_ROOT / ".env")
    async with mcp_tools() as tools:
        agent = build_agent(build_model(), tools)
        messages = await run_question(agent, question)
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                print(f"-> {call['name']}({call['args']})")
    print("\n" + final_answer(messages))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit('usage: python -m app.agents.baseline "<question>"')
    asyncio.run(main(sys.argv[1]))
