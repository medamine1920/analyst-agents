"""Queries translated to other languages: exact date handling per engine, names every engine accepts,
MongoDB pipelines built from the definitions, and (the real test of precision) a translation that runs
on another engine and returns the same rows."""

import asyncio
import json
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
import sqlglot
import yaml
from mcp import Client

from app.mcp_server.server import server
from app.semantic.translate import (
    SAMPLE_SEMANTIC,
    CannotTranslate,
    Definitions,
    to_mongo,
    to_sql,
    translate,
)

WAREHOUSE = Path(__file__).resolve().parents[1] / "warehouse" / "analyst.duckdb"
SQL = """SELECT DATE_TRUNC('week', ordered_at) AS metric_time__week, 1 AS __order_count
FROM "analyst"."main"."fct_orders" orders_src WHERE DATE_TRUNC('day', ordered_at) BETWEEN '2025-03-01' AND '2025-03-31'"""


def test_each_engine_gets_its_exact_date_functions_and_names_it_accepts():
    oracle = to_sql(SQL, "oracle")
    assert "TRUNC(ordered_at, 'IW')" in oracle and "DATE '2025-03-01'" in oracle  # ISO week; no NLS guessing
    assert " AS __order_count" not in oracle and " AS m__order_count" in oracle  # Oracle refuses a leading underscore
    assert "FROM fct_orders" in oracle and '"fct_orders"' not in oracle  # unquoted, so Oracle's upper case matches
    assert "DATETRUNC(iso_week, ordered_at)" in to_sql(SQL, "tsql")  # not the Sunday-based week
    assert "WEEK(MONDAY)" in to_sql(SQL, "bigquery")
    assert "WEEKDAY(ordered_at)" in to_sql(SQL, "mysql") and "0000" not in to_sql(SQL, "mysql")
    assert "'weekday 0', '-6 days'" in to_sql(SQL, "sqlite")
    block = to_sql(SQL, "plsql")
    assert block.startswith("BEGIN\n  FOR r IN (") and "DBMS_OUTPUT.PUT_LINE(TO_CHAR(r.metric_time__week)" in block
    for language, dialect in (("postgres", "postgres"), ("snowflake", "snowflake"), ("tsql", "tsql")):
        sqlglot.parse_one(to_sql(SQL, language), read=dialect)


def test_mongodb_pipelines_follow_the_definitions_and_refuse_what_they_cannot_say_exactly():
    definitions = Definitions(sorted(SAMPLE_SEMANTIC.glob("*.yml")))
    text = to_mongo({"metrics": ["average_order_value"], "group_by": ["store__store_name", "metric_time__week"],
                     "start_date": "2025-03-01", "end_date": "2025-03-31"}, definitions)
    assert text.startswith("db.fct_orders.aggregate(")
    pipeline = json.loads(text[len("db.fct_orders.aggregate("):-1].replace('ISODate("2025-03-01")', '"d1"')
                          .replace('ISODate("2025-04-01")', '"d2"'))
    assert pipeline[0] == {"$match": {"ordered_at": {"$gte": "d1", "$lt": "d2"}}}  # filter first, whole last day
    assert pipeline[1]["$lookup"]["from"] == "dim_stores"
    group = pipeline[3]["$group"]
    assert group["_id"]["metric_time__week"]["$dateTrunc"]["startOfWeek"] == "monday"
    assert group["m_subtotal"] == {"$sum": "$subtotal"} and group["m_order_count"] == {"$sum": 1}
    with pytest.raises(CannotTranslate, match="running total"):
        to_mongo({"metrics": ["cumulative_revenue"]}, definitions)
    with pytest.raises(CannotTranslate, match="SQL expression"):
        to_mongo({"metrics": ["new_customers"]}, definitions)  # case when is_first_order then 1 else 0 end


async def _query(arguments):
    async with Client(server) as client:
        result = await client.call_tool("query_metrics", {**arguments, "include_sql": True, "limit": 100})
        return json.loads(result.content[0].text)


@pytest.mark.integration
def test_a_translation_runs_on_another_engine_and_returns_the_same_rows(tmp_path):
    """The SQLite version of real queries, run on a SQLite copy of the warehouse tables."""
    copy = sqlite3.connect(tmp_path / "shop.sqlite")
    shutil.copy(WAREHOUSE, tmp_path / "warehouse.duckdb")  # the in-process MCP server holds the original
    with duckdb.connect(str(tmp_path / "warehouse.duckdb"), read_only=True) as source:
        for table in ("fct_orders", "dim_stores"):
            frame = source.execute(f"select * from {table}")
            columns = [d[0] for d in frame.description]
            rows = [[v.isoformat(sep=" ") if hasattr(v, "isoformat") else float(v) if isinstance(v, Decimal) else v
                     for v in row] for row in frame.fetchall()]
            copy.execute(f"create table {table} ({', '.join(columns)})")
            copy.executemany(f"insert into {table} values ({', '.join('?' * len(columns))})", rows)
    queries = [
        {"metrics": ["revenue_pre_tax", "order_count", "average_order_value"],
         "group_by": ["store__store_name", "metric_time__month"], "order_by": ["store__store_name", "metric_time__month"],
         "start_time": "2025-03-01", "end_time": "2025-05-31"},
        {"metrics": ["order_count"], "group_by": ["metric_time__week"], "order_by": ["metric_time__week"],
         "start_time": "2025-02-01", "end_time": "2025-03-31"},
        {"metrics": ["tax_collected"], "group_by": ["metric_time__quarter"], "order_by": ["metric_time__quarter"]},
    ]
    for arguments in queries:
        expected = asyncio.run(_query(arguments))
        got = copy.execute(to_sql(expected["sql"], "sqlite")).fetchall()
        assert len(got) == len(expected["rows"]), arguments
        for mine, theirs in zip(got, expected["rows"], strict=True):
            for a, b in zip(mine, theirs, strict=True):
                if isinstance(b, float):
                    assert a == pytest.approx(b, rel=1e-9)
                elif isinstance(b, str) and b[:4].isdigit():
                    assert str(a)[:10] == b[:10]  # the same period start
                else:
                    assert a == b


def test_the_api_translates_against_the_users_own_tables(tmp_path):
    folder = tmp_path / "src"
    (folder / "models").mkdir(parents=True)
    (folder / "models" / "sales_orders.sql").write_text(
        'select\n    "Amount" as amount,\n    "region" as region\nfrom live."sales"."orders"\n', encoding="utf-8")
    sql = 'SELECT region AS sales_orders__region, SUM(amount) AS total FROM "source"."main"."sales_orders" s GROUP BY region'
    result = translate("postgres", sql, None, folder)
    assert 'FROM sales.orders' in result["text"] and '"Amount" AS amount' in result["text"]  # runs on their database
    assert "live" not in result["text"] and "source" not in result["text"]
    with pytest.raises(CannotTranslate):
        translate("cobol", sql, None, folder)


def test_a_revenue_measure_of_quantity_times_price_becomes_a_multiplication(tmp_path):
    from app.sources.profile import Column, Table, model_sql, semantic_layer

    table = Table("lines", "raw.lines", "Lines", [Column("sold_on", "DATE"), Column("qty", "INTEGER"),
                                                  Column("price", "DOUBLE")])
    layer, _ = semantic_layer([table])
    layer.pop("time_dimensions")
    (tmp_path / "semantic.yml").write_text(yaml.safe_dump(layer), encoding="utf-8")
    definitions = Definitions([tmp_path / "semantic.yml"], {"lines": model_sql(table, "2026-01-01")})
    text = to_mongo({"metrics": ["lines_revenue"], "group_by": []}, definitions)
    assert text.startswith("db.lines.aggregate(")
    group = json.loads(text[len("db.lines.aggregate("):-1])[0]["$group"]
    assert group["m_lines_revenue"] == {"$sum": {"$multiply": ["$qty", "$price"]}}
