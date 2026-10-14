"""From table columns to a semantic layer: which columns are dates, groups or numbers, and which metrics follow.

Pure functions, no database access: the build step profiles the tables and hands the results here.
Generated metrics are deliberately plain (row counts, totals, averages, distinct counts), each with a
definition that says exactly how it is computed, because nobody has reviewed them the way the sample
shop's metrics were reviewed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NUMERIC_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
                 "UBIGINT", "FLOAT", "DOUBLE", "REAL", "DECIMAL", "NUMERIC")
TIME_TYPES = ("DATE", "TIMESTAMP", "DATETIME")
TEXT_TYPES = ("VARCHAR", "TEXT", "STRING", "CHAR", "BOOLEAN", "ENUM", "UUID")
MAX_GROUP_VALUES = 500   # a text column with more distinct values is an identifier, not a breakdown
MAX_METRICS = 80         # per source: past this the catalog stops being browsable
LOADED_ON = "loaded_on"  # a date added to tables that have none: MetricFlow needs one per table


@dataclass
class Column:
    name: str                    # safe name used in the semantic layer, e.g. order_date
    type: str
    distinct: int | None = None  # approximate, from a sample
    source: str = ""             # the name in the data, e.g. "Order Date" (defaults to name)

    def __post_init__(self) -> None:
        self.source = self.source or self.name


@dataclass
class Table:
    name: str             # model name in the generated project
    relation: str         # where the rows come from, e.g. raw.sales or live.public.orders
    label: str            # the name people know it by
    columns: list[Column] = field(default_factory=list)


def identifier(text: str, used: set[str] | None = None) -> str:
    """A safe lowercase name: 'Sales 2025.csv' -> 'sales_2025'. Unique within `used`."""
    name = re.sub(r"[^a-z0-9]+", "_", text.lower().rsplit(".", 1)[0] if "." in text else text.lower()).strip("_")
    name = name or "table"
    if name[0].isdigit():
        name = f"t_{name}"
    base, index = name, 2
    while used is not None and name in used:
        name, index = f"{base}_{index}", index + 1
    if used is not None:
        used.add(name)
    return name


def kind_of(column: Column) -> str:
    """time, number, group, key or skip."""
    base = column.type.upper().split("(")[0].strip()
    lowered = column.name.lower()
    is_key = lowered == "id" or lowered.endswith("_id")
    if base.startswith(TIME_TYPES):
        return "time"
    if base in NUMERIC_TYPES:
        return "key" if is_key else "number"
    if base in TEXT_TYPES:
        if is_key:
            return "key"
        return "group" if column.distinct is None or column.distinct <= MAX_GROUP_VALUES else "skip"
    return "skip"


def words(name: str) -> str:
    return name.replace("_", " ")


def semantic_layer(tables: list[Table]) -> tuple[dict, list[str]]:
    """The semantic models and metrics for these tables, plus notes on what was left out and why."""
    semantic_models, metrics, notes, table_time_dimensions = [], [], [], []
    for table in tables:
        kinds = {c.name: kind_of(c) for c in table.columns}
        times = [c for c, k in kinds.items() if k == "time"]
        time_column = times[0] if times else LOADED_ON
        if not times:
            notes.append(f"{table.label}: no date column, so its numbers cannot be split by period.")
        dimensions = [{"name": c, "type": "time", "type_params": {"time_granularity": "day"}} for c in times or [LOADED_ON]]
        dimensions += [{"name": c, "type": "categorical"} for c, k in kinds.items() if k == "group"]
        skipped = [c for c, k in kinds.items() if k == "skip"]
        if skipped:
            notes.append(f"{table.label}: not offered as breakdowns (too many different values or an unusual type): "
                         + ", ".join(skipped) + ".")
        measures = [{"name": f"m_{table.name}_rows", "agg": "sum", "expr": "1"}]
        table_metrics = [{"name": f"{table.name}_count", "label": f"{table.label}: rows",
                          "description": f"Number of rows in {table.label}.", "type": "simple",
                          "type_params": {"measure": f"m_{table.name}_rows"}}]
        for column, kind in kinds.items():
            if kind == "number":
                for agg, word, verb in (("sum", "total", "Sum"), ("average", "average", "Average")):
                    measures.append({"name": f"m_{table.name}_{column}_{agg}", "agg": agg, "expr": column})
                    table_metrics.append({
                        "name": f"{table.name}_{column}_{word}", "label": f"{table.label}: {word} {words(column)}",
                        "description": f"{verb} of {column} over the rows of {table.label}.", "type": "simple",
                        "type_params": {"measure": f"m_{table.name}_{column}_{agg}"}})
            elif kind == "key":
                measures.append({"name": f"m_{table.name}_{column}_distinct", "agg": "count_distinct", "expr": column})
                table_metrics.append({
                    "name": f"{table.name}_{column}_distinct", "label": f"{table.label}: distinct {words(column)}",
                    "description": f"Number of different values of {column} in {table.label}.", "type": "simple",
                    "type_params": {"measure": f"m_{table.name}_{column}_distinct"}})
        table_time_dimensions.append(f"{table.name}__{time_column}")
        semantic_models.append({
            "name": table.name, "description": f"Rows of {table.label}.", "model": f"ref('{table.name}')",
            "primary_entity": table.name, "defaults": {"agg_time_dimension": time_column},
            "dimensions": dimensions, "measures": measures})
        metrics.extend(table_metrics)
    if len(metrics) > MAX_METRICS:
        notes.append(f"Only the first {MAX_METRICS} of {len(metrics)} metrics are offered; "
                     "remove columns or tables you do not need to see the rest.")
        metrics = metrics[:MAX_METRICS]
    return {"semantic_models": semantic_models, "metrics": metrics, "time_dimensions": table_time_dimensions}, notes


def model_sql(table: Table, built_on: str) -> str:
    """The dbt model for one table: every column under its safe name, plus a load date if it has no date."""
    selected = ['    "{}" as {}'.format(c.source.replace('"', '""'), c.name) for c in table.columns]
    if not any(kind_of(c) == "time" for c in table.columns):
        selected.append(f"    cast('{built_on}' as date) as {LOADED_ON}")
    return "select\n" + ",\n".join(selected) + f"\nfrom {table.relation}\n"
