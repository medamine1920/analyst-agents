"""Integration test: the baseline graph + MCP adapter + real MCP server, with a scripted model.

No LLM API is called: a fake model emits one tool call, then a final answer.
This proves the wiring (tool schemas, tool execution, loop termination) works.
"""

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.baseline import build_agent, final_answer, run_question
from app.agents.mcp_tools import mcp_tools
from app.mcp_server.server import server

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def test_agent_calls_a_tool_and_answers():
    script = iter(
        [
            AIMessage("", tool_calls=[{"name": "query_metrics", "args": {"metrics": ["order_count"]}, "id": "c1"}]),
            AIMessage("There were 61,948 orders."),
        ]
    )
    async with mcp_tools(server) as tools:
        assert {tool.name for tool in tools} >= {"search_metrics", "query_metrics"}
        messages = await run_question(build_agent(ScriptedModel(messages=script), tools), "How many orders?")

    tool_results = [message for message in messages if isinstance(message, ToolMessage)]
    assert "61948" in tool_results[0].content
    assert final_answer(messages) == "There were 61,948 orders."


async def test_tool_errors_go_back_to_the_model():
    script = iter(
        [
            AIMessage("", tool_calls=[{"name": "query_metrics", "args": {"metrics": ["revenue"]}, "id": "c1"}]),
            AIMessage("That metric does not exist."),
        ]
    )
    async with mcp_tools(server) as tools:
        messages = await run_question(build_agent(ScriptedModel(messages=script), tools), "Revenue?")

    tool_result = next(message for message in messages if isinstance(message, ToolMessage))
    assert "search_metrics" in tool_result.content  # the helpful error reached the model
