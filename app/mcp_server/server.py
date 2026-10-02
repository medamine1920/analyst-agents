"""MCP server exposing the semantic layer as tools an agent can call.

Run locally over stdio (for MCP Inspector or an agent subprocess):
    python -m app.mcp_server.server

Run over HTTP (for deployment):
    MCP_TRANSPORT=streamable-http python -m app.mcp_server.server
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.semantic.layer import SemanticLayer

INSTRUCTIONS = """\
Governed business metrics for a coffee-and-sandwich chain (orders, items, customers, stores, products).
Workflow: search_metrics to find candidate metrics, list_dimensions to see valid group-bys,
then query_metrics. If several metrics match a vague term (for example "revenue"), do not guess:
report the options and their definitions. Call get_data_time_range before answering questions
about "last month" or "this year", because the data does not run up to today.
"""

server = MCPServer(name="analyst-semantic-layer", instructions=INSTRUCTIONS)


@lru_cache(maxsize=1)
def layer() -> SemanticLayer:
    """Load MetricFlow once, on first use, and reuse it for every call."""
    return SemanticLayer()


def anticipated(call: Callable[[], Any]) -> Any:
    """Run a tool body, turning expected failures into ToolError.

    MCP SDK v2 shows the model the message of a ToolError, but hides the text of any
    other exception (treated as a crash). Bad metric names, invalid group-bys and bad
    dates are expected mistakes an agent can fix, so their messages must get through.
    """
    try:
        return call()
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@server.tool()
def list_metrics() -> list[dict[str, str]]:
    """List every governed metric with its name, label, type and description."""
    return layer().list_metrics()


@server.tool()
def search_metrics(query: str, limit: int = 5) -> list[dict[str, str]]:
    """Find metrics matching a business term, such as "revenue" or "new customers".

    Returns the best matches first. Several results for one term usually means the
    term is ambiguous: compare their descriptions before choosing.
    """
    return layer().search_metrics(query, limit)


@server.tool()
def list_dimensions(metrics: list[str]) -> list[str]:
    """List group-by names valid for ALL the given metrics together.

    Names follow entity__dimension, for example store__store_name or product__product_type.
    Time dimensions accept a grain suffix: metric_time__day, __week, __month, __quarter, __year.
    """
    return anticipated(lambda: layer().list_dimensions(metrics))


@server.tool()
def get_data_time_range() -> dict[str, str]:
    """Return the first and last dates present in the data (ISO dates)."""
    return layer().data_time_range()


@server.tool()
def query_metrics(
    metrics: list[str],
    group_by: list[str] | None = None,
    where: list[str] | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    order_by: list[str] | None = None,
    limit: int = 100,
    include_sql: bool = False,
) -> dict[str, Any]:
    """Query one or more metrics and return columns and rows.

    group_by: names from list_dimensions, e.g. ["metric_time__month", "store__store_name"].
    where: filters using the Dimension template, e.g. ["{{ Dimension('store__store_name') }} = 'Brooklyn'"].
    start_time / end_time: ISO dates bounding metric_time. Always set end_time for cumulative
    metrics, otherwise they repeat their last value up to the end of the calendar table.
    order_by: metric or group-by names; prefix with "-" for descending, e.g. ["-revenue_pre_tax"].
    limit: maximum rows (capped at 500).
    include_sql: also return the SQL MetricFlow generated (long; only when you need to show it).
    """
    result = anticipated(lambda: layer().query(metrics, group_by, where, start_time, end_time, order_by, limit))
    if not include_sql:
        result.pop("sql")
    return result


@server.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Plain HTTP health check for Docker and deploys. Also warms up the semantic layer."""
    return JSONResponse({"status": "ok", "metrics": len(layer().list_metrics())})


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        server.run()
    else:
        server.run(
            transport="streamable-http",
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )


if __name__ == "__main__":
    main()
