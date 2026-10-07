"""Shared reliability harness for every agent in this project.

Two provider-level failure modes showed up in the baseline eval:
1. Malformed tool calls: gpt-oss on Groq sometimes leaks its internal message format into
   tool names (e.g. "search_metrics<|channel|>commentary"), and the API rejects the call
   with a 400 "tool call validation failed".
2. Runaway loops: the agent keeps calling tools until LangGraph's recursion limit crashes it.

Both the baseline and the multi-agent system use this module, so neither gets an unfair edge.
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

HARNESS_VERSION = "retry-and-step-budget"
MALFORMED_TOOL_CALL_MARKERS = ("tool call validation failed", "tool_use_failed", "failed to call a function")
MAX_ATTEMPTS = 3


def is_malformed_tool_call(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in MALFORMED_TOOL_CALL_MARKERS)


async def invoke_with_retry(model, messages: Sequence[BaseMessage], tool_names: Sequence[str]) -> AIMessage:
    """Call the model; if the provider rejects a malformed tool call, retry with a correction."""
    messages = list(messages)
    for attempt in range(MAX_ATTEMPTS):
        try:
            return await model.ainvoke(messages)
        except Exception as exc:
            if not is_malformed_tool_call(exc) or attempt == MAX_ATTEMPTS - 1:
                raise
            messages.append(
                SystemMessage(
                    "Your previous tool call was rejected: the tool name was invalid. "
                    f"Call tools using exactly one of these names: {', '.join(tool_names)}."
                )
            )
    raise AssertionError("unreachable")


async def force_final_answer(model: BaseChatModel, messages: Sequence[BaseMessage]) -> AIMessage:
    """Step budget used up: ask for the best answer from what was gathered, with no more tools."""
    response = await model.ainvoke(
        [
            *messages,
            SystemMessage(
                "You have reached the step limit. Do not call any more tools. Answer now using only "
                "the results gathered so far, and say clearly if any part of the question is unanswered."
            ),
        ]
    )
    if response.tool_calls:  # a model may still try; never let that reach the tools node
        return AIMessage(content=response.content or "I could not complete the analysis within the step limit.")
    return response
