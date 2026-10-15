"""A dashboard for each data source: headline numbers, a trend and breakdowns, chosen and computed in code.

No model is involved, so it costs no tokens. The headline numbers compare the last 30 days of data with
the 30 days before. For a live database the date range is read again on every refresh, so new rows
appear as they arrive; results are kept for CACHE_SECONDS so a page left open does not load the database.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import time
from typing import Any

from app.agents.sales_expert.expert import measure_of

GRAINS = ("day", "week", "month", "quarter", "year")
WINDOW_DAYS = 30
CACHE_SECONDS = 30
MAX_TILES = 4
MAX_GROUPS = 8
SPARK_POINTS = 26
# Headline measures in the order an analyst reads them, as the sales expert knows them.
TILE_MEASURES = ("revenue", "orders", "average_order_value", "customers", "gross_margin")
PERCENT = re.compile(r"(gross_margin|discount_rate|_share)$")  # ratios read as percentages
PREFERRED_GROUPS = ("store", "region", "channel", "product", "category", "segment", "country", "city", "type")


def tile_metrics(catalog: dict[str, dict]) -> list[str]:
    """The expert's headline measures that this source has; within a measure, its best-matching metric
    (order revenue before item revenue). Running totals are left out: over 30 days they add nothing."""
    candidates = [n for n, m in catalog.items() if m.get("type") != "cumulative" and "cumulative" not in n]
    chosen: list[str] = []
    for wanted in TILE_MEASURES:
        found = []
        for name in candidates:
            measure = measure_of(name, catalog[name].get("label") or "")
            if measure and measure["id"] == wanted:
                patterns = measure["patterns"]
                rank = next((i for i, p in enumerate(patterns) if p.search(name.lower())), len(patterns))
                found.append((rank, name))
        if found:
            chosen.append(min(found)[1])
    rows = next((n for n in candidates if n.endswith("_count") and n not in chosen), None)
    if len(chosen) < MAX_TILES and rows:
        chosen.append(rows)
    return chosen[:MAX_TILES] or candidates[:1]


def is_category(name: str) -> bool:
    last = name.split("__")[-1]
    return not name.startswith("metric_time") and last not in GRAINS and not last.endswith(("_at", "_on", "_date"))


def breakdown_dimensions(dimensions: list[str]) -> list[str]:
    categories = [d for d in dimensions if is_category(d)]
    ranked = sorted(categories, key=lambda d: next(
        (i for i, word in enumerate(PREFERRED_GROUPS) if word in d.split("__")[-1]), len(PREFERRED_GROUPS)))
    return ranked[:2]


def grain_for(first: str, last: str) -> str:
    days = (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days
    return "month" if days > 400 else "week" if days > 90 else "day"


def period_start(day: dt.date, grain: str) -> dt.date:
    if grain == "week":
        return day - dt.timedelta(days=day.weekday())  # Monday, as in the queries
    if grain == "month":
        return day.replace(day=1)
    return day


def next_period(day: dt.date, grain: str) -> dt.date:
    if grain == "month":
        return (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return day + dt.timedelta(days=7 if grain == "week" else 1)


def fill_periods(points: list[dict], first: str, last: str, grain: str, additive: bool) -> list[dict]:
    """Every period from first to last, so an empty one shows instead of being skipped by the line.

    An empty period is 0 for totals and counts, and has no value for averages and ratios.
    """
    by_start = {p["x"][:10]: p for p in points}
    day, end, filled = period_start(dt.date.fromisoformat(first), grain), dt.date.fromisoformat(last), []
    while day <= end:
        key = day.isoformat()
        filled.append(by_start.get(key) or {"x": f"{key}T00:00:00", "value": 0 if additive else None})
        day = next_period(day, grain)
    return filled


def change(now: float | None, before: float | None) -> float | None:
    if now is None or not before:
        return None
    return round(100 * (now - before) / abs(before), 1)


def _spark(trend: Any, time_column: str, metric: str, first: str, last: str, grain: str) -> list:
    """The last SPARK_POINTS periods of one headline number, for the small line on its tile."""
    if isinstance(trend, Exception):
        return []
    points = [{"x": r[time_column], "value": r[metric]} for r in _rows(trend) if r.get(metric) is not None]
    additive = not re.search(r"average|_per_|rate|margin|share|distinct|customers", metric)
    return [p["value"] for p in fill_periods(points, first, last, grain, additive)][-SPARK_POINTS:]


def _direction(metric: str, catalog: dict[str, dict]) -> bool | None:
    measure = measure_of(metric, catalog[metric].get("label") or "")
    return measure["higher_is_better"] if measure else True


def _rows(result: dict) -> list[dict]:
    return [dict(zip(result["columns"], row, strict=True)) for row in result["rows"]]


class Dashboards:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], tuple[float, dict]] = {}

    async def get(self, key: tuple[str, str], gateway: Any, live: bool) -> dict:
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < CACHE_SECONDS:
            return cached[1]
        board = await build(gateway, live)
        self._cache[key] = (time.time(), board)
        return board


async def build(gateway: Any, live: bool) -> dict:
    context = await gateway.context()
    catalog = context["catalog"]
    data_range = await gateway.call("get_data_time_range", {}) if live else context["data_range"]
    first, last = data_range["first_date"][:10], data_range["last_date"][:10]
    end = dt.date.fromisoformat(last)
    window = (end - dt.timedelta(days=WINDOW_DAYS - 1), end)
    before = (window[0] - dt.timedelta(days=WINDOW_DAYS), window[0] - dt.timedelta(days=1))
    tiles = tile_metrics(catalog)
    lead = tiles[0]
    grain = grain_for(first, last)
    dimensions = breakdown_dimensions(catalog[lead].get("group_by", []))

    def query(metrics: list[str], group_by: list[str] | None = None, period: tuple | None = None) -> Any:
        arguments: dict[str, Any] = {"metrics": metrics, "limit": 500}
        if group_by:
            arguments.update(group_by=group_by, order_by=group_by)
        if period:
            arguments.update(start_time=period[0].isoformat(), end_time=period[1].isoformat())
        return gateway.call("query_metrics", arguments)

    time_column = f"metric_time__{grain}"
    results = await asyncio.gather(
        query(tiles, period=window), query(tiles, period=before), query(tiles, [time_column]),
        *[query([lead], [d]) for d in dimensions], return_exceptions=True)
    now, then, trend, *breakdowns = results
    errors = [str(r)[:200] for r in results if isinstance(r, Exception)]

    def first_row(result: Any) -> dict:
        return {} if isinstance(result, Exception) else (_rows(result) or [{}])[0]

    current, previous = first_row(now), first_row(then)
    board = {
        "live": live, "updated_at": time.time(), "range": {"first": first, "last": last}, "grain": grain,
        "window": {"from": window[0].isoformat(), "to": window[1].isoformat(), "days": WINDOW_DAYS},
        "tiles": [{"metric": m, "label": catalog[m].get("label") or m, "description": catalog[m].get("description", ""),
                   "value": current.get(m), "previous": previous.get(m), "change_pct": change(current.get(m), previous.get(m)),
                   "percent": bool(PERCENT.search(m)), "higher_is_better": _direction(m, catalog),
                   "spark": _spark(trend, time_column, m, first, last, grain)} for m in tiles],
        "trend": None, "breakdowns": [], "errors": errors,
    }
    additive = not re.search(r"average|_per_|rate|margin|share", lead)  # sums and counts, not ratios
    if not isinstance(trend, Exception):
        points = [{"x": row[time_column], "value": row[lead]} for row in _rows(trend) if row[lead] is not None]
        board["trend"] = {"metric": lead, "label": catalog[lead].get("label") or lead, "dimension": time_column,
                          "points": fill_periods(points, first, last, grain, additive)}
    for dimension, result in zip(dimensions, breakdowns, strict=True):
        if isinstance(result, Exception):
            continue
        rows = sorted((r for r in _rows(result) if r[lead] is not None), key=lambda r: -r[lead])
        total = (sum(r[lead] for r in rows) or None) if additive else None  # a share of an average means nothing
        board["breakdowns"].append({
            "dimension": dimension, "metric": lead, "label": catalog[lead].get("label") or lead,
            "others": max(0, len(rows) - MAX_GROUPS),
            "points": [{"x": r[dimension], "value": r[lead],
                        "share_pct": round(100 * r[lead] / total, 1) if total else None} for r in rows[:MAX_GROUPS]]})
    return board
