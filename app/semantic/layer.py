"""A small, stable interface over MetricFlow.

The rest of the app (MCP server, agents, tests) talks to this class only,
never to MetricFlow internals. If MetricFlow's Python API changes, this is
the one file to update.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from dbt_metricflow.cli.cli_configuration import CLIConfiguration
from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow_semantics.errors.error_classes import InvalidQueryException

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT_DIR = REPO_ROOT / "warehouse"
MAX_ROWS = 500


def _to_json_safe(value: Any) -> Any:
    """Convert values MetricFlow returns into types JSON can carry."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def _parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}: use ISO format, e.g. 2025-08-31.") from exc


class SemanticLayer:
    """Read-only access to the governed metrics defined in the dbt project."""

    def __init__(self, project_dir: Path | None = None, db_path: Path | None = None) -> None:
        project_dir = Path(project_dir or os.environ.get("DBT_PROJECT_DIR") or DEFAULT_PROJECT_DIR)
        db_path = Path(db_path or os.environ.get("DUCKDB_PATH") or project_dir / "analyst.duckdb")
        if not db_path.exists():
            raise FileNotFoundError(f"{db_path} not found. Run `dbt build` in {project_dir} first.")

        # The serving profile reads DUCKDB_PATH and opens the file read-only.
        os.environ["DUCKDB_PATH"] = str(db_path)
        logging.getLogger("dbt").setLevel(logging.ERROR)

        config = CLIConfiguration()
        config.setup(
            dbt_profiles_path=project_dir / "serve",
            dbt_project_path=project_dir,
            configure_file_logging=False,
        )
        self._engine: MetricFlowEngine = config.mf
        self._metrics = {metric.name: metric for metric in self._engine.list_metrics()}

    def list_metrics(self) -> list[dict[str, str]]:
        """Every metric with its label, type and description."""
        return [
            {
                "name": metric.name,
                "label": metric.label or metric.name,
                "type": metric.type.value,
                "description": metric.description or "",
            }
            for metric in sorted(self._metrics.values(), key=lambda m: m.name)
        ]

    def search_metrics(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        """Rank metrics by how many query words appear in their name, label and description.

        Deliberately simple for Phase 1. Name and label matches count double,
        because they are the strongest signal of intent.
        """
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = []
        for metric in self.list_metrics():
            title = f"{metric['name']} {metric['label']}".lower()
            body = metric["description"].lower()
            score = sum(2 * (word in title) + (word in body) for word in words)
            if score:
                scored.append((score, metric))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
        return [metric for _, metric in scored[:limit]]

    def list_dimensions(self, metrics: list[str]) -> list[str]:
        """Group-by names valid for ALL the given metrics at once."""
        self._check_metrics(metrics)
        dimensions = self._engine.simple_dimensions_for_metrics(metrics)
        return sorted({dimension.dunder_name for dimension in dimensions})

    def data_time_range(self) -> dict[str, str]:
        """First and last order dates present in the data."""
        request = MetricFlowQueryRequest.create(group_by_names=["order_id__ordered_at"], min_max_only=True)
        first, last = self._engine.query(request).result_df.rows[0]
        return {"first_date": first.date().isoformat(), "last_date": last.date().isoformat()}

    def query(
        self,
        metrics: list[str],
        group_by: list[str] | None = None,
        where: list[str] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        order_by: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Run a metric query and return columns, rows and the SQL MetricFlow generated."""
        self._check_metrics(metrics)
        request = MetricFlowQueryRequest.create(
            metric_names=metrics,
            group_by_names=group_by or None,
            where_constraints=where or None,
            time_constraint_start=_parse_time(start_time),
            time_constraint_end=_parse_time(end_time),
            order_by_names=order_by or None,
            limit=max(1, min(limit, MAX_ROWS)),
        )
        try:
            result = self._engine.query(request)
        except InvalidQueryException as exc:
            # Re-raise as a plain ValueError so callers never depend on MetricFlow's exception types.
            # MetricFlow's message lists valid alternatives, which helps an agent correct itself.
            raise ValueError(str(exc)) from exc
        table = result.result_df
        return {
            "columns": list(table.column_names),
            "rows": [[_to_json_safe(value) for value in row] for row in table.rows],
            "row_count": table.row_count,
            "sql": result.sql,
        }

    def _check_metrics(self, metrics: list[str]) -> None:
        unknown = [name for name in metrics if name not in self._metrics]
        if unknown:
            raise ValueError(f"Unknown metric(s): {unknown}. Use search_metrics or list_metrics to find valid names.")
