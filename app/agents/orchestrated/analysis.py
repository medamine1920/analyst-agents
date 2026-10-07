"""Deterministic analysis: shares, formulas and period comparisons, computed in Python.

In the baseline the model did arithmetic in its head (and, on the share question,
reached for a calculator tool that did not exist). Here the model only names the formula.
"""

from __future__ import annotations

import ast
import operator

from app.agents.orchestrated.plan import Plan, QueryStep, is_time_dimension

OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
# Distinct-count metrics: summing them across groups double counts, so no shares or contributions.
NON_ADDITIVE = {"active_customers", "store_count"}


def additive_metrics(catalog: dict[str, dict]) -> set[str]:
    """Simple sum/count metrics can be split into shares; ratios, derived and distinct counts cannot."""
    return {name for name, info in catalog.items() if info["type"] == "simple" and name not in NON_ADDITIVE}


def evaluate(expression: str, values: dict[str, float | None]) -> float | None:
    """Evaluate +, -, *, / over named values. Anything else is rejected, so nothing can execute."""

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
            left, right = walk(node.left), walk(node.right)
            if left is None or right is None or (isinstance(node.op, ast.Div) and right == 0):
                return None
            return OPERATORS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            value = walk(node.operand)
            return None if value is None else -value
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise ValueError(f"Unknown name '{node.id}' in expression '{expression}'")
            return values[node.id]
        raise ValueError(f"Unsupported expression: '{expression}'")

    return walk(ast.parse(expression, mode="eval"))


def rows_as_dicts(table: dict) -> list[dict]:
    return [dict(zip(table["columns"], row, strict=True)) for row in table["rows"]]


def add_shares(step: QueryStep, rows: list[dict], additive: set[str]) -> list[dict]:
    """For a breakdown by one category, add each additive metric's share of its period total.

    Returns the period totals too, so the writer never has to add numbers itself.
    """
    categories = [g for g in step.group_by if not is_time_dimension(g)]
    times = [g for g in step.group_by if is_time_dimension(g)]
    if len(categories) != 1 or len(rows) < 2:
        return []
    totals_rows: dict[tuple, dict] = {}
    for metric in (m for m in step.metrics if m in additive):
        totals: dict[tuple, float] = {}
        for row in rows:
            key = tuple(row.get(t) for t in times)
            totals[key] = totals.get(key, 0.0) + (row.get(metric) or 0.0)
        for row in rows:
            total = totals[tuple(row.get(t) for t in times)]
            if row.get(metric) is not None and total:
                row[f"{metric}_share_pct"] = round(100 * row[metric] / total, 2)
        for key, total in totals.items():
            entry = totals_rows.setdefault(key, dict(zip(times, key, strict=True)))
            entry[f"{metric}_total"] = round(total, 4)
    return list(totals_rows.values())


def add_gaps(step: QueryStep, rows: list[dict]) -> None:
    """For a breakdown by one category, add each group's gap to the top group in the same period.

    Answers "which is higher, and by how much?" without the writer subtracting. Valid for any
    metric, including averages, because it compares values side by side rather than summing them.
    """
    categories = [g for g in step.group_by if not is_time_dimension(g)]
    times = [g for g in step.group_by if is_time_dimension(g)]
    if len(categories) != 1 or len(rows) < 2:
        return
    for metric in step.metrics:
        tops: dict[tuple, float] = {}
        for row in rows:
            value, key = row.get(metric), tuple(row.get(t) for t in times)
            if value is not None and (key not in tops or value > tops[key]):
                tops[key] = value
        for row in rows:
            value, key = row.get(metric), tuple(row.get(t) for t in times)
            if value is not None and key in tops:
                row[f"{metric}_gap_to_top"] = round(tops[key] - value, 4)


def groups_without_values(step: QueryStep, rows: list[dict]) -> list[dict]:
    """Groups present in a breakdown whose metric value is empty (e.g. a store with no orders)."""
    categories = [g for g in step.group_by if not is_time_dimension(g)]
    if len(categories) != 1:
        return []
    found = []
    for metric in step.metrics:
        empty = sorted(str(r[categories[0]]) for r in rows if r.get(metric) is None)
        if empty:
            found.append({"step": step.id, "metric": metric, "groups": empty})
    return found


def groups_missing_between(steps: list[QueryStep], rows_by_step: dict[str, list[dict]]) -> list[dict]:
    """Groups that appear in one breakdown but not in another over the same dimension."""
    found = []
    for a in steps:
        for b in steps:
            if a.id == b.id:
                continue
            dims_a = [g for g in a.group_by if not is_time_dimension(g)]
            dims_b = [g for g in b.group_by if not is_time_dimension(g)]
            if len(dims_a) != 1 or dims_a != dims_b or a.filters != b.filters:
                continue
            groups_a = {str(r[dims_a[0]]) for r in rows_by_step[a.id]}
            groups_b = {str(r[dims_b[0]]) for r in rows_by_step[b.id]}
            missing = sorted(groups_a - groups_b)
            if missing and groups_b < groups_a:
                found.append({"present_in": a.id, "absent_from": b.id, "dimension": dims_a[0], "groups": missing})
    return found


def compare(before: QueryStep, before_rows: list[dict], after_rows: list[dict], additive: set[str]) -> list[dict]:
    """Change per group between two periods, with each group's contribution to the total change."""
    keys = before.group_by
    index_before = {tuple(r.get(k) for k in keys): r for r in before_rows}
    index_after = {tuple(r.get(k) for k in keys): r for r in after_rows}
    groups = sorted(set(index_before) | set(index_after), key=str)
    output = []
    for metric in before.metrics:
        is_additive = metric in additive
        total_delta = None
        if is_additive:
            total_delta = sum((index_after.get(g, {}).get(metric) or 0) - (index_before.get(g, {}).get(metric) or 0)
                              for g in groups)
        for group in groups:
            old = index_before.get(group, {}).get(metric)
            new = index_after.get(group, {}).get(metric)
            if is_additive:  # a group absent in one period contributed zero there
                old, new = old or 0.0, new or 0.0
            if old is None or new is None:
                continue
            row = {**dict(zip(keys, group, strict=True)), "metric": metric, "before": round(old, 4),
                   "after": round(new, 4), "change": round(new - old, 4)}
            if old:
                row["change_pct"] = round(100 * (new - old) / old, 2)
            if is_additive and total_delta:
                row["share_of_total_change_pct"] = round(100 * (new - old) / total_delta, 2)
            output.append(row)
    return output


FIELD_GUIDE = {
    "<metric>_share_pct": "this row's share (%) of that metric's total across the breakdown, in the same period",
    "before / after": "the metric's value in the earlier / later step of a comparison",
    "change": "after minus before",
    "change_pct": "change as a percentage of before",
    "share_of_total_change_pct": "this group's share (%) of the total change across all groups",
    "before = 0": "the group had no activity in the earlier period (not proof that it opened then)",
    "<metric>_gap_to_top": "how far this group is below the top group for that metric, in the same period",
    "totals": "per-period totals of a breakdown's metrics (sum over all groups); cite these for totals",
    "groups_without_values": "groups listed in a breakdown whose metric is empty, i.e. no activity",
    "groups_missing_between_steps": "groups present in one breakdown but absent from another: no activity there",
}


def _rounded(value):
    return round(value, 4) if isinstance(value, float) else value


def build_facts(plan: Plan, results: dict[str, dict], catalog: dict[str, dict], context: dict) -> dict:
    """Everything the writer may use, in one JSON-ready structure."""
    additive = additive_metrics(catalog)
    rows_by_step = {step.id: rows_as_dicts(results[step.id]) for step in plan.steps}
    by_id = {step.id: step for step in plan.steps}
    totals_by_step = {step.id: add_shares(step, rows_by_step[step.id], additive) for step in plan.steps}
    for step in plan.steps:
        add_gaps(step, rows_by_step[step.id])
    empty_groups = [found for step in plan.steps for found in groups_without_values(step, rows_by_step[step.id])]
    missing_groups = groups_missing_between(plan.steps, rows_by_step)
    calculation_errors = []
    for calc in plan.calculations:
        for row in rows_by_step[calc.step_id]:
            try:
                value = evaluate(calc.expression, {m: row.get(m) for m in by_id[calc.step_id].metrics})
            except ValueError as exc:  # validation should prevent this; never let one formula end the run
                calculation_errors.append({"calculation": calc.name, "error": str(exc)})
                break
            row[calc.name] = None if value is None else round(value, 6)
    comparisons = [
        {"before": c.before_step, "after": c.after_step,
         "rows": compare(by_id[c.before_step], rows_by_step[c.before_step], rows_by_step[c.after_step], additive)}
        for c in plan.comparisons
    ]
    return {
        "data_range": context["data_range"],
        "definitions": {m: catalog[m]["description"] for s in plan.steps for m in s.metrics},
        "computed_fields": {**FIELD_GUIDE, **{c.name: f"computed as {c.expression}" for c in plan.calculations}},
        "steps": [
            {"id": s.id, "purpose": s.purpose, "period": [s.start_date, s.end_date],
             "filters": [f.model_dump() for f in s.filters],
             "rows": [{k: _rounded(v) for k, v in row.items()} for row in rows_by_step[s.id]],
             **({"totals": totals_by_step[s.id]} if totals_by_step[s.id] else {})}
            for s in plan.steps
        ],
        "comparisons": comparisons,
        **({"groups_without_values": empty_groups} if empty_groups else {}),
        **({"groups_missing_between_steps": missing_groups} if missing_groups else {}),
        **({"calculation_errors": calculation_errors} if calculation_errors else {}),
    }
