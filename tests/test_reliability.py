"""Tests for the shared reliability harness: retries, step budget, and no-crash guarantees."""

import itertools

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.reliability import invoke_with_retry, is_malformed_tool_call

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FlakyModel:
    """Raises a provider-style malformed-tool-call error N times, then answers."""

    def __init__(self, failures: int, error: str):
        self.failures, self.error, self.calls = failures, error, []

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        if len(self.calls) <= self.failures:
            raise RuntimeError(self.error)
        return AIMessage("ok")


MALFORMED = ("Error code: 400 - Tool call validation failed: attempted to call tool "
             "'search_metrics<|channel|>commentary' which was not in request.tools")


def test_malformed_tool_call_errors_are_recognized():
    assert is_malformed_tool_call(RuntimeError(MALFORMED))
    assert not is_malformed_tool_call(RuntimeError("Error code: 429 - rate limit reached"))


async def test_retry_recovers_and_tells_the_model_the_valid_names():
    model = FlakyModel(failures=1, error=MALFORMED)
    response = await invoke_with_retry(model, [], ["search_metrics", "query_metrics"])
    assert response.content == "ok"
    assert "search_metrics, query_metrics" in model.calls[1][-1].content


async def test_other_errors_are_not_retried():
    model = FlakyModel(failures=1, error="Error code: 429 - rate limit")
    with pytest.raises(RuntimeError, match="429"):
        await invoke_with_retry(model, [], ["query_metrics"])
    assert len(model.calls) == 1


async def test_retries_are_bounded():
    model = FlakyModel(failures=10, error=MALFORMED)
    with pytest.raises(RuntimeError):
        await invoke_with_retry(model, [], ["query_metrics"])
    assert len(model.calls) == 3


class LoopingModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.integration
async def test_endless_tool_loop_ends_with_an_answer_not_a_crash():
    from app.agents.baseline import (
        MAX_TOOL_ROUNDS,
        build_agent,
        final_answer,
        run_question,
    )
    from app.agents.mcp_tools import mcp_tools
    from app.mcp_server.server import server

    forever = (AIMessage("", tool_calls=[{"name": "get_data_time_range", "args": {}, "id": f"c{i}"}])
               for i in itertools.count())
    async with mcp_tools(server) as tools:
        messages = await run_question(build_agent(LoopingModel(messages=forever), tools), "Loop forever?")

    assert sum(isinstance(m, ToolMessage) for m in messages) == MAX_TOOL_ROUNDS
    assert final_answer(messages)  # a final, tool-free answer exists
