"""From table columns to a semantic layer: which columns are dates, groups or numbers, and which metrics follow.

Pure functions, no database access: the build step profiles the tables and hands the results here.
Generated metrics say exactly how they are computed, because nobody has reviewed them the way the sample
shop's metrics were reviewed. Column names are read the way a sales analyst would read them: money is
summed and averaged, prices and rates only averaged, codes and years become breakdowns, and order,
customer and product ids give the standard sales measures (average order value, revenue per customer,
units per order, gross profit and margin, discount rate).
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
LOADED_ON = "loaded_on"
# Names MetricFlow keeps for itself (time grains and its own time axis); a column called "day" is common.
RESERVED = {"nanosecond", "microsecond", "millisecond", "second", "minute", "hour", "day", "week", "month",
            "quarter", "year", "metric_time"}  # a date added to tables that have none: MetricFlow needs one per table


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


FILE_EXTENSIONS = {"csv", "tsv", "txt", "xlsx", "xls", "json", "sqlite", "sqlite3", "db", "duckdb", "parquet"}


def identifier(text: str, used: set[str] | None = None) -> str:
    """A safe lowercase name: 'Sales 2025.csv' -> 'sales_2025'. Unique within `used`."""
    lowered = text.lower()
    stem, dot, extension = lowered.rpartition(".")
    if dot and extension in FILE_EXTENSIONS:  # only a real file extension: "Rfam.family" keeps both words
        lowered = stem
    name = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    name = name or "table"
    if name[0].isdigit():
        name = f"t_{name}"
    if name in RESERVED:  # MetricFlow refuses these as names: a column called "day" becomes "day_data"
        name = f"{name}_data"
    base, index = name, 2
    while used is not None and name in used:
        name, index = f"{base}_{index}", index + 1
    if used is not None:
        used.add(name)
    return name


# Sales vocabulary, matched against the words of a column or table name ("unit_price" -> unit, price).
MONEY = {"amount", "revenue", "sales", "sale", "total", "subtotal", "price", "cost", "costs", "cogs", "profit",
         "margin", "discount", "tax", "fee", "fees", "value", "spend", "income", "payment", "paid", "net", "gross",
         "turnover", "mrr", "arr", "balance"}
REVENUE_WORDS = ("revenue", "sales", "turnover", "net", "amount", "total", "subtotal", "value", "paid", "payment")
NOT_REVENUE = {"cost", "costs", "cogs", "tax", "discount", "fee", "fees", "profit", "margin", "price", "balance"}
QUANTITY = {"qty", "quantity", "units", "unit", "items", "pieces", "volume", "seats", "licenses", "licences"}
RATE = {"rate", "ratio", "pct", "percent", "percentage", "share", "score", "rating", "stars", "probability"}
CODE = {"zip", "zipcode", "postal", "postcode", "phone", "year", "month", "week", "hour", "lat", "latitude",
        "lon", "lng", "longitude", "code"}
ORDER = {"order", "orders", "invoice", "invoices", "transaction", "transactions", "receipt", "receipts",
         "basket", "ticket", "booking", "bookings"}
LINE = {"line", "lines", "item", "items", "detail", "details"}
CUSTOMER = {"customer", "client", "buyer", "account", "user", "member", "shopper", "guest", "subscriber"}
MAX_CODE_GROUPS = 60     # a numeric code (zip, year) with at most this many values is offered as a breakdown


def name_words(name: str) -> set[str]:
    return set(name.lower().split("_"))


def is_key(column: Column) -> bool:
    lowered = column.name.lower()
    return lowered == "id" or lowered.endswith(("_id", "_no", "_number", "_key"))


def role_of(column: Column) -> str:
    """For a number: money, quantity, price, rate, code or number. For a key: order, customer or key."""
    found = name_words(column.name)
    if is_key(column):
        return "order" if found & ORDER else "customer" if found & CUSTOMER else "key"
    if found & RATE:
        return "rate"
    if found & CODE:
        return "code"
    if "price" in found and not found & {"total", "amount", "sum", "line"}:
        return "price"
    if found & QUANTITY:
        return "quantity"
    return "money" if found & MONEY else "number"


def kind_of(column: Column) -> str:
    """time, number, group, key or skip."""
    base = column.type.upper().split("(")[0].strip()
    if base.startswith(TIME_TYPES):
        return "time"
    if base in NUMERIC_TYPES:
        if is_key(column):
            return "key"
        if role_of(column) == "code":  # a year or a zip code is a way to split, not a number to add up
            return "group" if column.distinct is not None and column.distinct <= MAX_CODE_GROUPS else "skip"
        return "number"
    if base in TEXT_TYPES:
        if is_key(column):
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
        column_metrics = []
        by_name = {c.name: c for c in table.columns}
        for column, kind in kinds.items():
            if kind == "number":
                # Prices, rates and scores mean something on average only: adding them up does not.
                averaged_only = role_of(by_name[column]) in ("price", "rate")
                for agg, word, verb in (("sum", "total", "Sum"), ("average", "average", "Average")):
                    if agg == "sum" and averaged_only:
                        continue
                    measures.append({"name": f"m_{table.name}_{column}_{agg}", "agg": agg, "expr": column})
                    column_metrics.append({
                        "name": f"{table.name}_{column}_{word}", "label": f"{table.label}: {word} {words(column)}",
                        "description": f"{verb} of {column} over the rows of {table.label}.", "type": "simple",
                        "type_params": {"measure": f"m_{table.name}_{column}_{agg}"}})
            elif kind == "key":
                measures.append({"name": f"m_{table.name}_{column}_distinct", "agg": "count_distinct", "expr": column})
                column_metrics.append({
                    "name": f"{table.name}_{column}_distinct", "label": f"{table.label}: distinct {words(column)}",
                    "description": f"Number of different values of {column} in {table.label}.", "type": "simple",
                    "type_params": {"measure": f"m_{table.name}_{column}_distinct"}})
        sales, sales_measures = sales_metrics(table, kinds)
        measures += sales_measures
        table_metrics += sales + column_metrics
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


def sales_metrics(table: Table, kinds: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """The measures a sales analyst asks for first, when the columns allow them: (metrics, extra measures).

    Each is a ratio or a difference of metrics generated for the same table, so MetricFlow computes it
    from governed parts, and its description says exactly which.
    """
    t, label = table.name, table.label
    roles = {c.name: role_of(c) for c in table.columns if kinds.get(c.name) in ("number", "key")}
    numbers = [c for c, k in kinds.items() if k == "number"]

    def first(role: str) -> str | None:
        return next((c for c, r in roles.items() if r == role), None)

    def money_with(found: set[str]) -> str | None:
        return next((c for c in numbers if roles[c] == "money" and name_words(c) & found), None)

    revenue = next((c for word in REVENUE_WORDS for c in numbers
                    if roles[c] == "money" and word in name_words(c) and not name_words(c) & NOT_REVENUE), None)
    cost, discount = money_with({"cost", "costs", "cogs"}), money_with({"discount"})
    quantity, price, order, customer = first("quantity"), first("price"), first("order"), first("customer")
    metrics, measures = [], []

    def ratio(name: str, word: str, numerator: str, denominator: str, description: str) -> None:
        metrics.append({"name": f"{t}_{name}", "label": f"{label}: {word}", "description": description,
                        "type": "ratio", "type_params": {"numerator": numerator, "denominator": denominator}})

    revenue_metric = f"{t}_{revenue}_total" if revenue else None
    if not revenue and quantity and price:  # line revenue from its parts
        measures.append({"name": f"m_{t}_revenue", "agg": "sum", "expr": f"{quantity} * {price}"})
        metrics.append({"name": f"{t}_revenue", "label": f"{label}: revenue (quantity × price)",
                        "description": f"Sum of {quantity} × {price} over the rows of {label}.", "type": "simple",
                        "type_params": {"measure": f"m_{t}_revenue"}})
        revenue, revenue_metric = f"{quantity} × {price}", f"{t}_revenue"
    # Orders: distinct order ids, or the rows themselves when the table is named as one row per order.
    table_words, orders, orders_text = name_words(t), None, ""
    if order:
        orders, orders_text = f"{t}_{order}_distinct", f"the number of orders (distinct {order})"
    elif table_words & ORDER and not table_words & LINE:
        orders, orders_text = f"{t}_count", "the number of orders (rows)"
    customers = f"{t}_{customer}_distinct" if customer else None
    if revenue_metric and orders:
        ratio("average_order_value", "average order value", revenue_metric, orders,
              f"Total {revenue} divided by {orders_text}.")
    if revenue_metric and customers:
        ratio("revenue_per_customer", "revenue per customer", revenue_metric, customers,
              f"Total {revenue} divided by the number of customers (distinct {customer}).")
    if quantity and orders:
        ratio("units_per_order", "units per order", f"{t}_{quantity}_total", orders,
              f"Total {quantity} divided by {orders_text}.")
    if order and customers:
        ratio("orders_per_customer", "orders per customer", orders, customers,
              f"The number of orders (distinct {order}) divided by the number of customers (distinct {customer}).")
    if revenue_metric and cost:
        cost_metric = f"{t}_{cost}_total"
        metrics.append({"name": f"{t}_gross_profit", "label": f"{label}: gross profit",
                        "description": f"Total {revenue} minus total {cost}.", "type": "derived",
                        "type_params": {"expr": f"{revenue_metric} - {cost_metric}",
                                        "metrics": [{"name": revenue_metric}, {"name": cost_metric}]}})
        ratio("gross_margin", "gross margin", f"{t}_gross_profit", revenue_metric,
              f"Gross profit (total {revenue} minus total {cost}) divided by total {revenue}; 0.25 means 25%.")
    if revenue_metric and discount:
        ratio("discount_rate", "discount rate", f"{t}_{discount}_total", revenue_metric,
              f"Total {discount} divided by total {revenue}; 0.05 means 5%.")
    return metrics, measures


def model_sql(table: Table, built_on: str) -> str:
    """The dbt model for one table: every column under its safe name, plus a load date if it has no date."""
    selected = ['    "{}" as {}'.format(c.source.replace('"', '""'), c.name) for c in table.columns]
    if not any(kind_of(c) == "time" for c in table.columns):
        selected.append(f"    cast('{built_on}' as date) as {LOADED_ON}")
    return "select\n" + ",\n".join(selected) + f"\nfrom {table.relation}\n"
