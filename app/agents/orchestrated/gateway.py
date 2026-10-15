"""Typed access to the MCP server for the orchestrated agent.

Unlike the baseline, the model never types tool calls here: code calls the tools with
arguments taken from a validated plan, so malformed tool names cannot happen.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from app.agents.orchestrated.plan import QueryStep, is_time_dimension

MAX_ROWS_PER_STEP = 200
MAX_KNOWN_VALUES = 500  # past this many values a dimension is not checked (an id, not a category)


def match_value(value: str, known: list[str]) -> str | None:
    """The real value a filter means, when that is beyond doubt; otherwise None.

    MetricFlow answers a filter on 'brooklyn' (stored as 'Brooklyn') with no rows rather than an error,
    so an answer could be built on nothing. Repaired: a different case, the value inside a longer phrase
    ('Brooklyn store'), or one close spelling ('Brookln'). A value that matches nothing is left alone:
    "no rows for Boston" can be the true answer.
    """
    if value in known:
        return value
    folded = {k.casefold(): k for k in known}
    if value.casefold() in folded:
        return folded[value.casefold()]
    inside = [k for k in known if re.search(rf"(?<!\w){re.escape(k.casefold())}(?!\w)", value.casefold())]
    if len(inside) == 1:
        return inside[0]
    close = difflib.get_close_matches(value.casefold(), list(folded), n=2, cutoff=0.85)
    return folded[close[0]] if len(close) == 1 else None


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


class SourceUnreachable(ValueError):
    """The data source's database could not be reached (down, wrong address, refused): not a query mistake."""


class McpGateway:
    def __init__(self, client: Any) -> None:
        self._client = client
        self._context: dict | None = None
        self._dimensions: dict[tuple[str, ...], list[str]] = {}
        self._values: dict[str, list[str] | None] = {}

    async def call(self, name: str, arguments: dict) -> Any:
        result = await self._client.call_tool(name, arguments)
        if result.is_error:
            text = "\n".join(getattr(block, "text", "") for block in result.content)
            if "The database could not be reached" in text:
                raise SourceUnreachable(text[text.index("The database could not be reached"):])
            raise ValueError(text)
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

    async def values(self, dimension: str, metric: str) -> list[str] | None:
        """The text values a dimension takes (cached), or None when there are too many or they are not text."""
        if dimension not in self._values:
            result = await self.call("query_metrics", {"metrics": [metric], "group_by": [dimension],
                                                       "limit": MAX_KNOWN_VALUES + 1})
            column = result["columns"].index(dimension)
            found = [row[column] for row in result["rows"] if isinstance(row[column], str)]
            self._values[dimension] = found if 0 < len(found) <= MAX_KNOWN_VALUES else None
        return self._values[dimension]

    async def resolve_filters(self, step: QueryStep) -> None:
        """Replace each filter value with the real value it clearly means (see match_value)."""
        for item in step.filters:
            if is_time_dimension(item.dimension) or re.fullmatch(r"-?[\d.]+|true|false", item.value.lower()):
                continue
            known = await self.values(item.dimension, step.metrics[0])
            matched = match_value(item.value, known) if known else None
            if matched:
                item.value = matched

    async def run_step(self, step: QueryStep) -> dict:
        # The SQL is kept for the person (to read or translate), never shown to the model.
        arguments: dict[str, Any] = {"metrics": step.metrics, "limit": MAX_ROWS_PER_STEP, "include_sql": True}
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
