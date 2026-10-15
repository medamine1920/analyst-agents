"""A question's queries in other languages: SQL dialects, an Oracle PL/SQL block, and MongoDB.

SQL: MetricFlow's generated SQL (DuckDB) is parsed and rewritten by sqlglot, so the translation is
mechanical, not guessed. For a user's own source the generated views are inlined, so the result runs
on the user's database as it is; the sample shop's SQL keeps its warehouse table names. Every output is
parsed back in its own dialect before it is returned.

MongoDB: built from the plan step and the metric definitions (a pipeline cannot be derived from SQL).
What cannot be expressed exactly (running totals, measures written as SQL expressions, metrics from
two collections) is refused with the reason, never approximated.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import re
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp

# id: (label, sqlglot dialect)
SQL_LANGUAGES = {
    "postgres": ("PostgreSQL", "postgres"),
    "mysql": ("MySQL", "mysql"),
    "tsql": ("SQL Server (T-SQL)", "tsql"),
    "oracle": ("Oracle SQL", "oracle"),
    "plsql": ("Oracle PL/SQL block", "oracle"),
    "snowflake": ("Snowflake", "snowflake"),
    "bigquery": ("BigQuery", "bigquery"),
    "sqlite": ("SQLite", "sqlite"),
    "duckdb": ("DuckDB (as run)", "duckdb"),
}
LANGUAGES = {**{key: label for key, (label, _) in SQL_LANGUAGES.items()}, "mongodb": "MongoDB aggregation"}
GRAINS = ("day", "week", "month", "quarter", "year")


class CannotTranslate(ValueError):
    """The query cannot be written exactly in that language; the message says why."""


# ---- SQL ----------------------------------------------------------------------------------------

def _strip_catalog(tree: exp.Expression, catalog: str) -> exp.Expression:
    for table in tree.find_all(exp.Table):
        if table.catalog == catalog:
            table.set("catalog", None)
        elif table.db == "raw":  # a loaded CSV file: the user's table is named after the file
            table.set("db", None)
    return tree


def portable(sql: str, models: dict[str, str] | None = None) -> exp.Expression:
    """MetricFlow's SQL without comments or warehouse prefixes; `models` (view name -> its SELECT) are inlined."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    for table in list(tree.find_all(exp.Table)):
        view = (models or {}).get(table.name)
        if view is not None:
            inner = _strip_catalog(sqlglot.parse_one(view, read="duckdb"), "live")
            table.replace(exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(table.alias_or_name))))
        else:
            table.set("catalog", None)
            table.set("db", None)
    for node in tree.walk():
        node.comments = None
    return tree


# Truncating a date to a period, written the way each engine documents it. Weeks start on Monday, as in
# DuckDB (where the answers were computed). sqlglot's own versions are wrong for several engines (Oracle
# and SQLite get BigQuery's function, MySQL counts from year 0, BigQuery and SQL Server weeks start on Sunday).
TRUNCATE = {
    "duckdb": {g: f"DATE_TRUNC('{g}', {{x}})" for g in GRAINS},
    "postgres": {g: f"DATE_TRUNC('{g}', {{x}})" for g in GRAINS},
    "snowflake": {g: f"DATE_TRUNC('{g.upper()}', {{x}})" for g in GRAINS},
    # On DATE values, so the result compares with DATE literals (BigQuery will not mix them with TIMESTAMP).
    "bigquery": {**{g: f"DATE_TRUNC(DATE({{x}}), {g.upper()})" for g in GRAINS},
                 "week": "DATE_TRUNC(DATE({x}), WEEK(MONDAY))"},
    "tsql": {**{g: f"DATETRUNC({g}, {{x}})" for g in GRAINS}, "week": "DATETRUNC(iso_week, {x})"},
    "oracle": {"day": "TRUNC({x}, 'DD')", "week": "TRUNC({x}, 'IW')", "month": "TRUNC({x}, 'MM')",
               "quarter": "TRUNC({x}, 'Q')", "year": "TRUNC({x}, 'YYYY')"},
    "mysql": {"day": "DATE({x})", "week": "DATE_SUB(DATE({x}), INTERVAL WEEKDAY({x}) DAY)",
              "month": "CAST(DATE_FORMAT({x}, '%Y-%m-01') AS DATE)",
              "quarter": "MAKEDATE(YEAR({x}), 1) + INTERVAL (QUARTER({x}) - 1) QUARTER",
              "year": "MAKEDATE(YEAR({x}), 1)"},
    "sqlite": {"day": "DATE({x})", "week": "DATE({x}, 'weekday 0', '-6 days')",
               "month": "DATE({x}, 'start of month')",
               "quarter": "DATE({x}, 'start of year', printf('+%d months', (CAST(strftime('%m', {x}) AS INTEGER) - 1) / 3 * 3))",
               "year": "DATE({x}, 'start of year')"},
}
# A date written so that no session setting can change how it is read.
DATE_LITERAL = {"oracle": "DATE '{d}'", "sqlite": "DATE('{d}')", "bigquery": "DATE '{d}'"}
SIMPLE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _exact(tree: exp.Expression, dialect: str) -> tuple[exp.Expression, dict[str, str]]:
    """Swap date truncations and date literals for placeholders filled with each engine's exact form."""
    filled: dict[str, str] = {}

    def placeholder(text: str) -> exp.Expression:
        key = f"EXACTPART{len(filled)}"
        filled[key + "()"] = text
        return exp.Anonymous(this=key)

    for node in list(tree.find_all(exp.DateTrunc, exp.TimestampTrunc)):
        grain = node.text("unit").lower() or node.args["unit"].name.lower()
        if grain not in GRAINS:
            raise CannotTranslate(f"truncating a date to '{grain}' has no exact form here")
        node.replace(placeholder(TRUNCATE[dialect][grain].format(x=node.this.sql(dialect))))
    for node in list(tree.find_all(exp.Literal)):
        if node.is_string and re.fullmatch(r"\d{4}-\d{2}-\d{2}", node.this):
            node.replace(placeholder(DATE_LITERAL.get(dialect, "CAST('{d}' AS DATE)").format(d=node.this)))
    return tree, filled


def _plain_names(tree: exp.Expression) -> exp.Expression:
    """Names every engine accepts: no leading underscores (Oracle and BigQuery refuse them), and simple
    lowercase names unquoted, so Oracle and Snowflake match tables created without quotes."""
    for identifier in tree.find_all(exp.Identifier):
        name = identifier.this
        if name.startswith("_"):
            identifier.set("this", "m" + name)
        if SIMPLE_NAME.match(identifier.this):
            identifier.set("quoted", False)
    return tree


def to_sql(sql: str, language: str, models: dict[str, str] | None = None) -> str:
    dialect = SQL_LANGUAGES[language][1]
    tree = _plain_names(portable(sql, models))
    sqlglot.parse_one(tree.sql(dialect=dialect), read=dialect)  # it must read back in its own dialect
    tree, filled = _exact(tree, dialect)
    text = tree.sql(dialect=dialect, pretty=True)
    for key, value in filled.items():
        text = text.replace(key, value)
    if language == "plsql":
        columns = [select.alias_or_name for select in tree.selects]
        line = " || ' | ' || ".join(f"TO_CHAR(r.{name})" for name in columns) or "'(no columns)'"
        body = "\n".join("    " + row for row in text.splitlines())
        text = (f"BEGIN\n  FOR r IN (\n{body}\n  ) LOOP\n    DBMS_OUTPUT.PUT_LINE({line});\n  END LOOP;\nEND;\n/")
    return text


# ---- MongoDB -------------------------------------------------------------------------------------

class Definitions:
    """Semantic models and metrics from YAML, plus how each model maps to a collection and its fields."""

    def __init__(self, yaml_files: list[Path], model_sql: dict[str, str] | None = None) -> None:
        self.models: dict[str, dict] = {}
        self.metrics: dict[str, dict] = {}
        for path in yaml_files:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            self.models.update({m["name"]: m for m in doc.get("semantic_models", [])})
            self.metrics.update({m["name"]: m for m in doc.get("metrics", [])})
        self.measures = {measure["name"]: (model, measure) for model in self.models.values()
                         for measure in model.get("measures", [])}
        self.model_sql = model_sql or {}

    def collection(self, model: dict) -> tuple[str, dict[str, str]]:
        """(collection name, field name -> source field) for a semantic model."""
        view = re.search(r"ref\('([^']+)'\)", model["model"]).group(1)
        sql = self.model_sql.get(view)
        if not sql:
            return view, {}
        tree = sqlglot.parse_one(sql, read="duckdb")
        fields = {select.alias_or_name: select.this.name for select in tree.selects
                  if isinstance(select, exp.Alias) and isinstance(select.this, exp.Column)}
        return tree.find(exp.Table).name, fields


def _field(fields: dict[str, str], name: str, prefix: str = "") -> str:
    return "$" + prefix + fields.get(name, name)


def _accumulator(measure: dict, fields: dict[str, str], prefix: str) -> tuple[dict, dict | None]:
    """($group accumulator, $project expression or None to take it as is)."""
    expr, agg = str(measure.get("expr", measure["name"])).strip(), measure["agg"]
    if measure.get("agg_params") or measure.get("non_additive_dimension"):
        raise CannotTranslate(f"the measure {measure['name']} has settings MongoDB cannot express exactly")
    if re.fullmatch(r"-?\d+(\.\d+)?", expr):
        value = float(expr) if "." in expr else int(expr)
        if agg in ("sum", "count"):
            return {"$sum": value}, None
        raise CannotTranslate(f"the measure {measure['name']} aggregates a constant with {agg}")
    product = re.fullmatch(r"([A-Za-z_]\w*) \* ([A-Za-z_]\w*)", expr)  # e.g. quantity * price
    if product and agg == "sum":
        return {"$sum": {"$multiply": [_field(fields, product[1], prefix), _field(fields, product[2], prefix)]}}, None
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr):
        raise CannotTranslate(f"the measure {measure['name']} is a SQL expression ({expr}), which has no exact "
                              "MongoDB equivalent; use a SQL translation")
    field = _field(fields, expr, prefix)
    if agg == "sum":
        return {"$sum": field}, None
    if agg == "average":
        return {"$avg": field}, None
    if agg in ("min", "max"):
        return {f"${agg}": field}, None
    if agg == "count":
        return {"$sum": {"$cond": [{"$ne": [{"$ifNull": [field, None]}, None]}, 1, 0]}}, None
    if agg == "count_distinct":
        return {"$addToSet": field}, {"$size": "$__VALUE__"}
    raise CannotTranslate(f"the aggregation {agg} has no exact MongoDB equivalent")


def _combine(node: ast.AST, names: dict[str, str]) -> object:
    """A derived metric's arithmetic as a MongoDB expression."""
    if isinstance(node, ast.Expression):
        return _combine(node.body, names)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return {"$multiply": [-1, _combine(node.operand, names)]}
    if isinstance(node, ast.BinOp):
        ops = {ast.Add: "$add", ast.Sub: "$subtract", ast.Mult: "$multiply"}
        left, right = _combine(node.left, names), _combine(node.right, names)
        if isinstance(node.op, ast.Div):  # SQL gives NULL on division by zero; so does this
            return {"$cond": [{"$eq": [right, 0]}, None, {"$divide": [left, right]}]}
        if type(node.op) in ops:
            return {ops[type(node.op)]: [left, right]}
    raise CannotTranslate("the metric's formula uses something other than + - * /")


def to_mongo(step: dict, definitions: Definitions, limit: int = 100) -> str:
    """A mongosh `db.<collection>.aggregate([...])` for one plan step."""
    accumulators: dict[str, dict] = {}
    outputs: dict[str, object] = {}
    base: dict | None = None

    def use_measure(name: str) -> str:
        nonlocal base
        model = definitions.measures[name][0]
        if base is not None and model["name"] != base["name"]:
            raise CannotTranslate("the metrics come from different collections; MongoDB would need a join "
                                  "per metric, so use a SQL translation")
        base = model
        key = name if name.startswith("m_") else f"m_{name}"
        if key not in accumulators:
            accumulators[key] = name  # resolved once the base collection is known
        return key

    def metric_value(name: str) -> object:
        metric = definitions.metrics.get(name)
        if not metric:
            raise CannotTranslate(f"no definition for the metric {name}")
        params = metric.get("type_params", {})
        if metric.get("filter") or params.get("filter"):
            raise CannotTranslate(f"the metric {name} has a filter written in SQL")
        kind = metric["type"]
        if kind == "simple":
            measure = params["measure"]
            return "$" + use_measure(measure if isinstance(measure, str) else measure["name"])
        if kind == "ratio":
            num, den = metric_value(_name(params["numerator"])), metric_value(_name(params["denominator"]))
            return {"$cond": [{"$eq": [den, 0]}, None, {"$divide": [num, den]}]}
        if kind == "derived":
            names = {_alias(m): metric_value(m["name"]) for m in params["metrics"]}
            return _combine(ast.parse(params["expr"], mode="eval"), names)
        raise CannotTranslate(f"the metric {name} is a {kind} metric (a running total), which MongoDB's "
                              "aggregation cannot reproduce exactly")

    for name in step["metrics"]:
        outputs[name] = metric_value(name)
    collection, fields = definitions.collection(base)

    # Measures -> $group accumulators (distinct counts become sets, sized afterwards).
    group_fields: dict[str, dict] = {}
    for key, measure_name in accumulators.items():
        accumulator, after = _accumulator(definitions.measures[measure_name][1], fields, "")
        group_fields[key] = accumulator
        if after is not None:
            sized = json.loads(json.dumps(after).replace("$__VALUE__", "$" + key))
            outputs = json.loads(json.dumps(outputs).replace(f'"${key}"', json.dumps(sized)))

    time_field = _field(fields, base["defaults"]["agg_time_dimension"])
    lookups, group_id = [], {}
    for name in step.get("group_by", []):
        group_id[name] = _dimension(name, base, definitions, fields, time_field, lookups)

    match: dict = {}
    for item in step.get("filters", []):
        expression = _dimension(item["dimension"], base, definitions, fields, time_field, lookups)
        if not isinstance(expression, str):
            raise CannotTranslate("a filter on a date grain; use start and end dates instead")
        match[expression[1:]] = _literal(item["value"])
    if step.get("start_date") or step.get("end_date"):
        window = {}
        if step.get("start_date"):
            window["$gte"] = {"$date": step["start_date"]}
        if step.get("end_date"):
            after = dt.date.fromisoformat(step["end_date"]) + dt.timedelta(days=1)
            window["$lt"] = {"$date": after.isoformat()}  # the whole last day, as in the SQL
        match[time_field[1:]] = window

    # Filters on the collection's own fields run first (they can use its indexes); then the joins,
    # then filters on joined fields.
    joined = {lookup["$lookup"]["as"] for lookup in lookups if "$lookup" in lookup}
    own = {k: v for k, v in match.items() if k.split(".")[0] not in joined}
    later = {k: v for k, v in match.items() if k not in own}
    pipeline: list[dict] = [*([{"$match": own}] if own else []), *lookups, *([{"$match": later}] if later else [])]
    pipeline.append({"$group": {"_id": group_id or None, **group_fields}})
    pipeline.append({"$project": {"_id": 0, **{name: f"$_id.{name}" for name in group_id}, **outputs}})
    if group_id:
        pipeline.append({"$sort": {name: 1 for name in group_id}})
    pipeline.append({"$limit": limit})
    text = json.dumps(pipeline, indent=2)
    text = re.sub(r'\{\s*"\$date": "([^"]+)"\s*\}', r'ISODate("\1")', text)
    return f"db.{collection}.aggregate({text})"


def _name(ref) -> str:
    return ref if isinstance(ref, str) else ref["name"]


def _alias(ref: dict) -> str:
    return ref.get("alias") or ref["name"]


def _literal(value: str) -> object:
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def _dimension(name: str, base: dict, definitions: Definitions, fields: dict, time_field: str,
               lookups: list[dict]) -> object:
    """The MongoDB expression for a group-by name such as store__store_name or metric_time__month."""
    parts = name.split("__")
    grain = parts[-1] if parts[-1] in GRAINS else None
    parts = parts[:-1] if grain else parts
    if parts == ["metric_time"]:
        if not grain:
            raise CannotTranslate("metric_time needs a grain")
        return _truncate(time_field, grain)
    if len(parts) != 2:
        raise CannotTranslate(f"the dimension {name} goes through more than one join")
    entity, dimension = parts
    own = {e["name"]: e for e in base.get("entities", [])}
    if entity == base.get("primary_entity") or own.get(entity, {}).get("type") == "primary":
        value = _field(fields, dimension)
    elif entity in own:  # another collection, reached through this one's foreign key
        target = next((m for m in definitions.models.values() for e in m.get("entities", [])
                       if e["name"] == entity and e["type"] == "primary"), None)
        if not target:
            raise CannotTranslate(f"nothing to join for {entity}")
        collection, target_fields = definitions.collection(target)
        primary = next(e for e in target["entities"] if e["name"] == entity)
        if not any(step.get("as") == entity for step in (lookup["$lookup"] for lookup in lookups)):
            lookups += [{"$lookup": {"from": collection, "localField": own[entity].get("expr", entity),
                                     "foreignField": primary.get("expr", entity), "as": entity}},
                        {"$unwind": {"path": f"${entity}", "preserveNullAndEmptyArrays": True}}]
        value = _field(target_fields, dimension, f"{entity}.")
    else:
        raise CannotTranslate(f"the dimension {name} is not reachable from this collection")
    time_dimensions = {d["name"] for d in base.get("dimensions", []) if d.get("type") == "time"}
    if grain or dimension in time_dimensions:
        return _truncate(value, grain or "day")
    return value


def _truncate(field: str, grain: str) -> dict:
    spec = {"date": field, "unit": grain}
    if grain == "week":
        spec["startOfWeek"] = "monday"  # as in the SQL; MongoDB would start on Sunday
    return {"$dateTrunc": spec}


# ---- one call for the API -------------------------------------------------------------------------

SAMPLE_SEMANTIC = Path(__file__).resolve().parents[2] / "warehouse" / "models" / "semantic"
NOTES = {
    "tsql": "DATETRUNC needs SQL Server 2022 or later.",
    "plsql": "Run with SET SERVEROUTPUT ON to see the rows.",
    "bigquery": "Prefix each table with its dataset (dataset.table).",
    "mongodb": "One collection per table, with the same field names.",
}


def translate(language: str, sql: str | None, step: dict | None, folder: Path | None = None) -> dict:
    """{"language", "label", "text", "note"} for one query; `folder` is a user's source, None for the sample.

    A user's source is translated against its own tables (its generated views are inlined); the sample
    keeps the warehouse's table names.
    """
    if language not in LANGUAGES:
        raise CannotTranslate(f"choose one of: {', '.join(LANGUAGES)}")
    models = {}
    if folder is not None:
        models = {path.stem: path.read_text(encoding="utf-8") for path in (folder / "models").glob("*.sql")
                  if path.stem != "metricflow_time_spine"}
    notes = [NOTES.get(language, "")]
    if folder is None:
        notes.append("Table names are the sample warehouse's (fct_orders, dim_stores, …).")
    elif (folder / "files").exists() and any((folder / "files").glob("*.csv")):
        notes.append("Each CSV file is read as a table named after the file.")
    if language == "mongodb":
        if not step:
            raise CannotTranslate("this query has no plan to build a pipeline from")
        files = sorted(SAMPLE_SEMANTIC.glob("*.yml")) if folder is None else [folder / "models" / "semantic.yml"]
        text = to_mongo(step, Definitions(files, models))
    else:
        if not sql:
            raise CannotTranslate("this query has no SQL saved (it ran before queries were kept)")
        text = to_sql(sql, language, models)
    return {"language": language, "label": LANGUAGES[language], "text": text,
            "note": " ".join(n for n in notes if n)}
