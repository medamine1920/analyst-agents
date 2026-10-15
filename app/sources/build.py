"""Build one data source into a queryable semantic layer. Runs in its own process:

    python -m app.sources.build <source folder>

It reads source.json in that folder, loads CSV files into a DuckDB workspace (or attaches a database
read-only), profiles every table, writes a dbt + MetricFlow project, builds it, and writes
result.json with what it found. A live database's connection string comes from the SOURCE_ATTACH
environment variable, so no password is ever written to a file.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

import duckdb
import yaml

from app.sources.profile import (
    Column,
    Table,
    identifier,
    kind_of,
    model_sql,
    role_of,
    semantic_layer,
)

MAX_TABLES = 25
SAMPLE_ROWS = 20000  # distinct counts are estimated on this many rows, so a huge live table stays cheap
EXTENSIONS = {"postgres": "postgres", "mysql": "mysql", "sqlite": "sqlite"}
TIME_SPINE_SQL = (
    "{{ config(materialized='table') }}\n"
    "with days as ({{ dbt.date_spine('day', \"cast('1990-01-01' as date)\", \"cast('2040-01-01' as date)\") }})\n"
    "select cast(date_day as date) as date_day from days\n"
)


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def attach_entry(spec: dict) -> dict | None:
    """How dbt (and later the query process) attaches the source database, read-only."""
    attach = spec.get("attach")
    if not attach:
        return None
    path = "{{ env_var('SOURCE_ATTACH') }}" if attach.get("secret") else attach["path"]
    return {"path": path, "type": attach["type"], "alias": "live", "read_only": True}


def open_workspace(folder: Path, spec: dict) -> tuple[duckdb.DuckDBPyConnection, list[tuple[str, str]]]:
    """The DuckDB workspace with the source's tables in it: (relation, label) for each."""
    workspace = folder / "source.duckdb"
    workspace.unlink(missing_ok=True)
    con = duckdb.connect(str(workspace))
    tables: list[tuple[str, str]] = []
    if spec["kind"] == "csv":
        con.execute("create schema raw")
        used: set[str] = set()
        for file in spec["files"]:
            name = identifier(file, used)
            path = (folder / "files" / file).as_posix()
            con.execute(f"create table raw.{name} as select * from read_csv_auto('{path}', header = true)")
            tables.append((f"raw.{name}", file.rsplit(".", 1)[0]))
        return con, tables
    attach = spec["attach"]
    if attach["type"] in EXTENSIONS:
        con.execute(f"install {EXTENSIONS[attach['type']]}; load {EXTENSIONS[attach['type']]};")
    path = os.environ["SOURCE_ATTACH"] if attach.get("secret") else attach["path"]
    con.execute(f"attach '{path.replace(chr(39), chr(39) * 2)}' as live (type {attach['type']}, read_only)")
    wanted_schema = attach.get("schema")
    wanted_tables = {t.lower() for t in attach.get("tables") or []}  # only these, when the user named some
    rows = con.execute("select table_schema, table_name from information_schema.tables "
                       "where table_catalog = 'live' order by table_schema, table_name").fetchall()
    for schema, table in rows:
        if wanted_schema and schema != wanted_schema:
            continue
        if schema in ("information_schema", "pg_catalog", "mysql", "performance_schema", "sys"):
            continue
        if wanted_tables and table.lower() not in wanted_tables and f"{schema}.{table}".lower() not in wanted_tables:
            continue
        tables.append((f"live.{quoted(schema)}.{quoted(table)}", table if schema in ("main", "public") else f"{schema}.{table}"))
    if wanted_tables and not tables:
        raise ValueError("None of the tables you named were found: " + ", ".join(sorted(wanted_tables)))
    return con, tables[:MAX_TABLES]


def profile(con: duckdb.DuckDBPyConnection, relation: str, label: str, used: set[str]) -> Table:
    described = con.execute(f"describe select * from {relation}").fetchall()
    names: set[str] = set()
    columns = [Column(name=identifier(row[0], names), type=str(row[1]), source=row[0]) for row in described]
    # Text columns and numeric codes (years, zip codes) need a distinct count: it decides whether they
    # are breakdowns or identifiers.
    texts = [c for c in columns if c.type.upper().split("(")[0] != "BOOLEAN"
             and (kind_of(c) == "group" or role_of(c) == "code" and kind_of(c) == "skip")]
    if texts:
        estimates = ", ".join(f"approx_count_distinct({quoted(c.source)})" for c in texts)
        sample = f"(select * from {relation} limit {SAMPLE_ROWS})"
        for column, distinct in zip(texts, con.execute(f"select {estimates} from {sample}").fetchone(), strict=True):
            column.distinct = int(distinct or 0)
    return Table(name=identifier(label, used), relation=relation, label=label, columns=columns)


def write_project(folder: Path, spec: dict, tables: list[Table], layer: dict) -> None:
    models = folder / "models"
    shutil.rmtree(models, ignore_errors=True)
    models.mkdir(parents=True)
    (folder / "serve").mkdir(exist_ok=True)
    name = f"src_{spec['id']}"
    (folder / "dbt_project.yml").write_text(yaml.safe_dump({
        "name": name, "version": "1.0.0", "profile": "source", "model-paths": ["models"],
        "models": {name: {"+materialized": "view"}}}), encoding="utf-8")
    attach = attach_entry(spec)
    build = {"type": "duckdb", "path": str(folder / "source.duckdb"), "threads": 1, **({"attach": [attach]} if attach else {})}
    serve = {"type": "duckdb", "path": "{{ env_var('DUCKDB_PATH') }}", "threads": 1,
             "config_options": {"access_mode": "READ_ONLY"}, **({"attach": [attach]} if attach else {})}
    (folder / "profiles.yml").write_text(yaml.safe_dump({"source": {"target": "build", "outputs": {"build": build}}}),
                                         encoding="utf-8")
    (folder / "serve" / "profiles.yml").write_text(
        yaml.safe_dump({"source": {"target": "serve", "outputs": {"serve": serve}}}), encoding="utf-8")
    built_on = dt.datetime.now(dt.UTC).date().isoformat()
    for table in tables:
        (models / f"{table.name}.sql").write_text(model_sql(table, built_on), encoding="utf-8")
    (models / "metricflow_time_spine.sql").write_text(TIME_SPINE_SQL, encoding="utf-8")
    spine = {"name": "metricflow_time_spine", "time_spine": {"standard_granularity_column": "date_day"},
             "columns": [{"name": "date_day", "granularity": "day"}]}
    (models / "semantic.yml").write_text(yaml.safe_dump({"models": [spine], **layer}, sort_keys=False),
                                         encoding="utf-8")


def run_dbt(folder: Path) -> str | None:
    """None when the build worked, otherwise what went wrong, in a sentence or two."""
    from dbt.cli.main import dbtRunner

    result = dbtRunner().invoke(["build", "--project-dir", str(folder), "--profiles-dir", str(folder), "--quiet"])
    if result.success:
        return None
    if result.exception:
        return str(result.exception)[:600]
    messages = [f"{r.node.name}: {r.message}" for r in (result.result or []) if str(r.status) in ("error", "fail")]
    return "; ".join(messages)[:600] or "The build failed."


def build(folder: Path) -> dict:
    spec = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    try:
        con, relations = open_workspace(folder, spec)
        if not relations:
            return {"ok": False, "error": "No tables were found in this source."}
        used: set[str] = set()
        tables, skipped = [], []
        for relation, label in relations:
            try:
                tables.append(profile(con, relation, label, used))
            except duckdb.Error as exc:  # e.g. a read-only account that may see a table but not read it
                reason = str(exc).rsplit("ERROR:", 1)[-1].strip()  # the database's own words, not the query
                skipped.append(f"{label} was skipped: {reason[:200]}")
        if not tables:
            return {"ok": False, "error": "None of the tables could be read. " + " ".join(skipped)}
        rows = {}
        if spec["kind"] == "csv":  # counting is cheap for loaded files; live tables are left alone
            rows = {t.name: con.execute(f"select count(*) from {t.relation}").fetchone()[0] for t in tables}
        con.close()
        layer, notes = semantic_layer(tables)
        notes = skipped + notes
        time_dimensions = layer.pop("time_dimensions")
        write_project(folder, spec, tables, layer)
        problem = run_dbt(folder)
        if problem:
            return {"ok": False, "error": problem}
        return {"ok": True, "notes": notes, "metrics": len(layer["metrics"]), "time_dimensions": time_dimensions,
                "tables": [{"name": t.name, "label": t.label, "columns": len(t.columns), "rows": rows.get(t.name)}
                           for t in tables]}
    except Exception as exc:  # noqa: BLE001 - every failure becomes a readable status, never a crash
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"}


if __name__ == "__main__":
    target = Path(sys.argv[1])
    outcome = build(target)
    (target / "result.json").write_text(json.dumps(outcome), encoding="utf-8")
    sys.exit(0 if outcome["ok"] else 1)
