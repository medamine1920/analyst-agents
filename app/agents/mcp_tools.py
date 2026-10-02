"""Give LangChain/LangGraph agents the MCP server's tools.

langchain-mcp-adapters pins `mcp<2`, while this project uses MCP SDK v2,
so this small adapter does the same job without that dependency:
it lists the server's tools and wraps each one as a LangChain StructuredTool
that forwards calls to the open MCP session.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.tools import StructuredTool, ToolException
from mcp import Client, StdioServerParameters


def default_target() -> str | StdioServerParameters:
    """MCP_URL if set (e.g. http://localhost:8000/mcp), else launch the local server over stdio."""
    url = os.environ.get("MCP_URL")
    if url:
        return url
    return StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server.server"])


def _result_text(result: Any) -> str:
    if result.structured_content is not None:
        return json.dumps(result.structured_content, default=str)
    return "\n".join(getattr(block, "text", "") for block in result.content)


def _wrap(client: Client, tool: Any) -> StructuredTool:
    async def call(**arguments: Any) -> str:
        arguments = {key: value for key, value in arguments.items() if value is not None}
        result = await client.call_tool(tool.name, arguments)
        text = _result_text(result)
        if result.is_error:
            # Returned to the model as the tool's answer, so it can correct its call.
            raise ToolException(text)
        return text

    return StructuredTool.from_function(
        coroutine=call,
        name=tool.name,
        description=tool.description or "",
        args_schema=tool.input_schema,
        handle_tool_error=True,
    )


@asynccontextmanager
async def mcp_tools(target: str | StdioServerParameters | Any | None = None) -> AsyncIterator[list[StructuredTool]]:
    """Open one MCP session and yield its tools; the session closes when the block exits."""
    async with Client(target or default_target()) as client:
        listed = await client.list_tools()
        yield [_wrap(client, tool) for tool in listed.tools]
