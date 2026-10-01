"""Integration tests: call the MCP tools exactly as an agent would, in-process."""

import pytest
from mcp import Client

from app.mcp_server.server import server

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_server_exposes_the_five_tools():
    async with Client(server) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
    assert tools == {"list_metrics", "search_metrics", "list_dimensions", "get_data_time_range", "query_metrics"}


async def test_query_tool_returns_rows_and_sql():
    async with Client(server) as client:
        result = await client.call_tool(
            "query_metrics",
            {"metrics": ["order_count"], "group_by": ["store__store_name"], "order_by": ["-order_count"]},
        )
    assert not result.is_error
    data = result.structured_content
    assert data["columns"] == ["store__store_name", "order_count"]
    assert data["row_count"] >= 1
    assert "select" in data["sql"].lower()


async def test_bad_metric_is_reported_as_a_tool_error():
    async with Client(server) as client:
        result = await client.call_tool("query_metrics", {"metrics": ["revenue"]})
    assert result.is_error
