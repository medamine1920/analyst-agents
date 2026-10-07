"""Typed access to the MCP server for the orchestrated agent.

Unlike the baseline, the model never types tool calls here: code calls the tools with
arguments taken from a validated plan, so malformed tool names cannot happen.
"""

from __future__ import annotations

from typing import Any

from app.agents.orchestrated.plan import QueryStep

MAX_ROWS_PER_STEP = 200


def _where(dimension: str, value: str) -> str:
    if value.lower() in ("true", "false"):
        literal = value.lower()
    else:
        try:
            float(value)
            literal = value
        except ValueError:
            literal = "'" + value.replace("'", "''") + "'"
    return f"{{{{ Dimension('{dimension}') }}}} = {literal}"


class McpGateway:
    def __init__(self, client: Any) -> None:
        self._client = client
        self._context: dict | None = None
        self._dimensions: dict[tuple[str, ...], list[str]] = {}

    async def call(self, name: str, arguments: dict) -> Any:
        result = await self._client.call_tool(name, arguments)
        if result.is_error:
            raise ValueError("\n".join(getattr(block, "text", "") for block in result.content))
        data = result.structured_content
        return data["result"] if isinstance(data, dict) and set(data) == {"result"} else data

    async def context(self) -> dict:
        """Data range and metric catalog (with each metric's valid group-bys), loaded once."""
        if self._context is None:
            catalog = {}
            for metric in await self.call("list_metrics", {}):
                metric["group_by"] = await self.dimensions([metric["name"]])
                catalog[metric["name"]] = metric
            self._context = {"data_range": await self.call("get_data_time_range", {}), "catalog": catalog}
        return self._context

    async def dimensions(self, metrics: list[str]) -> list[str]:
        key = tuple(sorted(metrics))
        if key not in self._dimensions:
            self._dimensions[key] = await self.call("list_dimensions", {"metrics": list(key)})
        return self._dimensions[key]

    def cached_dimensions(self, metrics: list[str]) -> list[str]:
        """Group-bys valid for all the metrics, from cache (intersection of per-metric lists)."""
        key = tuple(sorted(metrics))
        if key in self._dimensions:
            return self._dimensions[key]
        catalog = self._context["catalog"] if self._context else {}
        sets = [set(catalog[m]["group_by"]) for m in metrics if m in catalog]
        return sorted(set.intersection(*sets)) if sets else []

    async def run_step(self, step: QueryStep) -> dict:
        arguments: dict[str, Any] = {"metrics": step.metrics, "limit": MAX_ROWS_PER_STEP}
        if step.group_by:
            arguments["group_by"] = step.group_by
            arguments["order_by"] = step.group_by
        if step.filters:
            arguments["where"] = [_where(f.dimension, f.value) for f in step.filters]
        if step.start_date:
            arguments["start_time"] = step.start_date
        if step.end_date:
            arguments["end_time"] = step.end_date
        return await self.call("query_metrics", arguments)
