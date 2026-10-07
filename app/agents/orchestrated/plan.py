"""The plan the model writes, and the code that checks and completes it.

The model decides WHAT to look at (metrics, breakdowns, periods, formulas).
Code checks every name against the semantic layer, then adds what good analysis
always needs, so a plan can be wrong in a recoverable way but never silently shallow.
"""

from __future__ import annotations

import ast
import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

TIME_GRAINS = ("day", "week", "month", "quarter", "year")
STORE = "store__store_name"
VOLUME_METRICS = ["order_count", "average_order_value"]
ORDER_REVENUE = {"revenue_pre_tax", "revenue_with_tax"}
# Metrics whose time axis is not an event date: store_count is dated by each store's opening.
# Filtering them by an order period silently removes everything, so they are counted over all time.
TIMELESS_METRICS = {"store_count"}


class Filter(BaseModel):
    dimension: str = Field(description="A group-by name, e.g. store__store_name")
    value: str = Field(description="Exact value, e.g. Brooklyn")


class QueryStep(BaseModel):
    id: str = Field(description="Short unique id, e.g. feb, mar, by_store")
    purpose: str = Field(description="Why this query is needed")
    metrics: list[str]
    group_by: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    start_date: str | None = Field(default=None, description="ISO date, inclusive")
    end_date: str | None = Field(default=None, description="ISO date, inclusive")


class Calculation(BaseModel):
    name: str = Field(description="Name for the computed column, e.g. effective_tax_rate")
    step_id: str
    expression: str = Field(description="Arithmetic over that step's metric names, e.g. tax_collected / revenue_pre_tax")


class Comparison(BaseModel):
    before_step: str
    after_step: str


class Plan(BaseModel):
    question_type: Literal["lookup", "ranking", "comparison", "change_explanation", "share", "listing"]
    steps: list[QueryStep]
    calculations: list[Calculation] = Field(default_factory=list)
    comparisons: list[Comparison] = Field(default_factory=list)


ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub,
                 ast.Constant, ast.Name, ast.Load)


def expression_problem(expression: str, names: list[str]) -> str | None:
    """Why a calculation can't run, or None. Mirrors the analysis evaluator, but at planning time."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return "it is not a valid arithmetic expression"
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            return ("it refers to another step (a dotted name). Calculations can only use metric names "
                    "of their own step; to compare two steps, add a comparison instead (it computes "
                    "change and change_pct)")
        if not isinstance(node, ALLOWED_NODES):
            return "only numbers, metric names and + - * / are allowed"
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            return "only numeric constants are allowed"
        if isinstance(node, ast.Name) and node.id not in names:
            return f"'{node.id}' is not a metric of that step (use one of {names})"
    return None


def expand_time_names(dimensions: list[str]) -> set[str]:
    """list_dimensions returns e.g. metric_time__day; accept every grain of each time dimension."""
    allowed = set(dimensions)
    for name in dimensions:
        base = name[: -len("__day")] if name.endswith("__day") else name if name.endswith("_at") else None
        if base:
            allowed.add(base)
            allowed.update(f"{base}__{grain}" for grain in TIME_GRAINS)
    return allowed


def is_time_dimension(name: str) -> bool:
    return name.startswith("metric_time") or "_at" in name.split("__")[-1] or name.split("__")[-1] in TIME_GRAINS


def validate_plan(plan: Plan, metric_names: set[str], dimensions_for) -> list[str]:
    """Return human-readable errors; an empty list means the plan can run."""
    errors, step_ids = [], [step.id for step in plan.steps]
    if not plan.steps:
        errors.append("The plan has no steps.")
    if len(set(step_ids)) != len(step_ids):
        errors.append("Step ids must be unique.")
    for step in plan.steps:
        unknown = [m for m in step.metrics if m not in metric_names]
        if unknown:
            errors.append(f"Step {step.id}: unknown metric(s) {unknown}.")
            continue
        allowed = expand_time_names(dimensions_for(step.metrics))
        for name in step.group_by + [f.dimension for f in step.filters]:
            if name not in allowed:
                errors.append(f"Step {step.id}: '{name}' is not valid for {step.metrics}. Valid: {sorted(allowed)}")
        for label, value in (("start_date", step.start_date), ("end_date", step.end_date)):
            if value:
                try:
                    dt.date.fromisoformat(value)
                except ValueError:
                    errors.append(f"Step {step.id}: {label} '{value}' is not an ISO date (YYYY-MM-DD).")
    by_id = {step.id: step for step in plan.steps}
    for calc in plan.calculations:
        if calc.step_id not in by_id:
            errors.append(f"Calculation {calc.name}: unknown step {calc.step_id}.")
            continue
        problem = expression_problem(calc.expression, by_id[calc.step_id].metrics)
        if problem:
            errors.append(f"Calculation {calc.name} ('{calc.expression}'): {problem}.")
    for comparison in plan.comparisons:
        before, after = by_id.get(comparison.before_step), by_id.get(comparison.after_step)
        if not before or not after:
            errors.append(f"Comparison {comparison.before_step} vs {comparison.after_step}: unknown step.")
        elif before.metrics != after.metrics or before.group_by != after.group_by:
            errors.append(f"Comparison {before.id} vs {after.id}: both steps need the same metrics and group_by.")
        elif any(is_time_dimension(name) for name in before.group_by):
            errors.append(f"Comparison {before.id} vs {after.id}: compare periods with dates, not a time group_by.")
    return errors


def apply_ambiguity(plan: Plan, ambiguities: list[dict]) -> Plan:
    """Every step using one definition of an ambiguous term gets all of its definitions."""
    for ambiguity in ambiguities:
        candidates = ambiguity["metrics"]
        for step in plan.steps:
            if any(m in candidates for m in step.metrics):
                step.metrics += [m for m in candidates if m not in step.metrics]
    return plan


def ensure_decomposition(plan: Plan, dimensions_for) -> Plan:
    """Explaining a change always means asking: which store, and more orders or bigger orders?"""
    by_id = {step.id: step for step in plan.steps}
    for comparison in list(plan.comparisons):
        before, after = by_id[comparison.before_step], by_id[comparison.after_step]
        if ORDER_REVENUE & set(before.metrics):
            for step in (before, after):
                step.metrics += [m for m in VOLUME_METRICS if m not in step.metrics]
        already_by_store = STORE in before.group_by or any(f.dimension == STORE for f in before.filters)
        if not already_by_store and STORE in expand_time_names(dimensions_for(before.metrics)):
            pair = []
            for step in (before, after):
                copy = step.model_copy(deep=True)
                copy.id, copy.purpose = f"{step.id}_by_store", f"{step.purpose} (broken down by store)"
                copy.group_by = [*step.group_by, STORE]
                plan.steps.append(copy)
                pair.append(copy.id)
            plan.comparisons.append(Comparison(before_step=pair[0], after_step=pair[1]))
    return plan


def normalize_dates(plan: Plan, first_date: str, last_date: str) -> Plan:
    """Drop date ranges that filter nothing, and never date-filter timeless metrics.

    A range covering all the data changes nothing for order metrics, but erases metrics measured on
    another time axis (store openings in 2016-2017 fall outside an order period in 2024-2025).
    """
    for step in list(plan.steps):
        covers_all = (not step.start_date or step.start_date <= first_date) and (
            not step.end_date or step.end_date >= last_date
        )
        if covers_all:
            step.start_date = step.end_date = None
            continue
        timeless = [m for m in step.metrics if m in TIMELESS_METRICS]
        if not timeless or not (step.start_date or step.end_date):
            continue
        others = [m for m in step.metrics if m not in TIMELESS_METRICS]
        if not others:
            step.start_date = step.end_date = None
            continue
        split = step.model_copy(deep=True)
        split.id, split.metrics = f"{step.id}_all_time", timeless
        split.start_date = split.end_date = None
        split.purpose = f"{step.purpose} (counted over all time)"
        step.metrics = others
        plan.steps.append(split)
    return plan
