"""The sales expert: what a senior sales analyst brings to each question, applied in code.

It knows the measures (knowledge.yml): their definitions, the words people use for them, which way is
good, and which chart answers which kind of question. It never calls a model, so it costs no tokens and
always gives the same advice for the same data. Four jobs:

- planner_notes: the sales words in a question and the metric each one means here.
- advise: definitions and direction for the metrics an answer uses, the chart for each result, and
  follow-up questions. The direction also tells the console when a rise is bad (costs, returns).
- writer_notes: what each metric means and which way is good, so the answer says "improved" correctly.
- measure_of: the expert's reading of one metric, used by the dashboard to pick and colour its numbers.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

KNOWLEDGE = Path(__file__).with_name("knowledge.yml")
GRAINS = ("day", "week", "month", "quarter", "year")
NOT_ADDITIVE = re.compile(r"average|_per_|rate|ratio|share|margin|pct|_distinct$")
MAX_LINES = 4  # chart lines before a heatmap reads better (the palette has four categorical colours)


@lru_cache(maxsize=1)
def knowledge() -> dict:
    data = yaml.safe_load(KNOWLEDGE.read_text(encoding="utf-8"))
    for measure in data["measures"]:
        measure["patterns"] = [re.compile(p) for p in measure["match"]]
    return data


def measure_of(metric: str, label: str = "") -> dict | None:
    """The measure a metric is, from its name (then its label), or None if the expert does not know it."""
    for text in (metric.lower(), re.sub(r"\W+", "_", label.lower()).strip("_")):
        for measure in knowledge()["measures"]:
            if text and any(p.search(text) for p in measure["patterns"]):
                return measure
    return None


def known(catalog: dict[str, dict]) -> dict[str, dict]:
    return {name: m for name, info in catalog.items() if (m := measure_of(name, info.get("label") or ""))}


def is_time(dimension: str) -> bool:
    last = dimension.split("__")[-1]
    return dimension.startswith("metric_time") or last in GRAINS or last.endswith(("_at", "_on", "_date"))


def additive(metric: str, catalog: dict[str, dict]) -> bool:
    """A sum or a count (can be split into parts), not a ratio, an average or a distinct count."""
    measure = measure_of(metric, (catalog.get(metric) or {}).get("label") or "")
    if measure and measure["unit"] not in ("money", "count", "quantity") or measure and measure["id"] == "customers":
        return False
    return (catalog.get(metric) or {}).get("type", "simple") == "simple" and not NOT_ADDITIVE.search(metric)


def words(name: str) -> str:
    """store__store_name -> "store", product__product_type -> "product type": how a person names the group."""
    return re.sub(r"_(name|id|code|label)$", "", name.split("__")[-1]).replace("_", " ")


# ---- for the planner ----------------------------------------------------------------------------

def planner_notes(question: str, catalog: dict[str, dict]) -> str:
    """Lines for the planner: each sales word in the question, what it means, and the metrics measuring it.

    Empty when the question uses none, so ordinary questions cost no extra tokens.
    """
    text, by_measure, lines = question.lower(), {}, []
    for name, measure in known(catalog).items():
        by_measure.setdefault(measure["id"], []).append(name)
    for measure in knowledge()["measures"]:
        said = next((a for a in measure["aliases"]
                     if re.search(rf"(?<![\w]){re.escape(a.lower())}(?![\w])", text)), None)
        if not said:
            continue
        metrics = by_measure.get(measure["id"], [])
        if metrics:
            lines.append(f'- "{said}" is {measure["label"].lower()}: {measure["definition"]} '
                         f'Measured here by {", ".join(metrics[:3])}.')
        elif measure.get("formula"):
            lines.append(f'- "{said}" is {measure["label"].lower()} ({measure["formula"]}); no single metric here '
                         "measures it, so compute it with a calculation from the metrics that do.")
    return ("Sales vocabulary in this question:\n" + "\n".join(lines)) if lines else ""


# ---- for the answer and the console --------------------------------------------------------------

def choose_chart(group_by: list[str], rows: list[dict], metric: str, question_type: str,
                 catalog: dict[str, dict]) -> tuple[str | None, str]:
    """(chart, why) for one result; None when a table says it better."""
    charts = knowledge()["charts"]
    times = [g for g in group_by if is_time(g)]
    categories = [g for g in group_by if not is_time(g)]
    sums = additive(metric, catalog)
    if not group_by:
        return "tiles", charts["tiles"]
    if len(group_by) == 1 and categories:
        values = [r.get(metric) for r in rows]
        groups = len(rows)
        if question_type == "share" and sums and groups >= 3 and all(isinstance(v, (int, float)) and v >= 0 for v in values):
            return "treemap", charts["treemap"]
        return "bar", charts["bar"]
    if len(group_by) == 1 and times:
        return ("area", charts["area"]) if sums else ("line", charts["line"])
    if len(times) == 1 and len(categories) == 1:
        groups = len({r.get(categories[0]) for r in rows})
        if groups > MAX_LINES:
            return "heatmap", charts["heatmap"]
        if sums and question_type == "share":
            return "stacked_area", charts["stacked_area"]
        return "lines", charts["lines"]
    return None, "Two breakdowns at once read best as a table."


def advise(question: str, question_type: str, plan_steps: list[dict], facts: dict, catalog: dict[str, dict]) -> dict:
    """The expert's notes on one answer: definitions, the chart for each result, follow-up questions."""
    metrics = list(dict.fromkeys(m for step in plan_steps for m in step["metrics"]))
    definitions = []
    for metric in metrics:
        measure = measure_of(metric, (catalog.get(metric) or {}).get("label") or "")
        if measure:
            definitions.append({"metric": metric, "label": (catalog.get(metric) or {}).get("label") or measure["label"],
                                "measure": measure["label"], "definition": measure["definition"],
                                "formula": measure.get("formula"), "higher_is_better": measure["higher_is_better"],
                                "advice": measure.get("advice")})
    rows = {s["id"]: s.get("rows", []) for s in facts.get("steps", [])}
    charts = {}
    for step in plan_steps:
        kind, why = choose_chart(step.get("group_by", []), rows.get(step["id"], []), step["metrics"][0],
                                 question_type, catalog)
        if kind:
            charts[step["id"]] = {"kind": kind, "why": why}
    for comparison in facts.get("comparisons", []):
        changed = [r for r in comparison["rows"] if "change" in r]
        if changed and additive(changed[0]["metric"], catalog) and len(changed) > 1:
            charts[f"{comparison['before']}->{comparison['after']}"] = {
                "kind": "waterfall", "why": knowledge()["charts"]["waterfall"]}
    return {"definitions": definitions, "charts": charts, "follow_ups": follow_ups(question, plan_steps, catalog)}


def follow_ups(question: str, plan_steps: list[dict], catalog: dict[str, dict], limit: int = 3) -> list[str]:
    """Next questions an analyst would ask, from the measures used and the breakdowns available."""
    suggestions: list[str] = []
    asked, seen = question.lower(), set()
    for step in plan_steps:
        for metric in step["metrics"]:
            info = catalog.get(metric) or {}
            measure = measure_of(metric, info.get("label") or "")
            if not measure or measure["id"] in seen:  # one set per measure, not per definition of it
                continue
            seen.add(measure["id"])
            groups = [g for g in step.get("group_by", []) + info.get("group_by", []) if not is_time(g)]
            label = (info.get("label") or measure["label"]).lower()
            for template in measure.get("follow_ups", []):
                if "{group}" in template and not groups:
                    continue
                text = template.format(label=label, group=words(groups[0]) if groups else "")
                if text.lower() not in asked and text not in suggestions:
                    suggestions.append(text)
    return suggestions[:limit]


def writer_notes(metrics: list[str], catalog: dict[str, dict]) -> dict[str, str]:
    """For the writer: what each metric means and which way is good (no numbers, so nothing to check)."""
    notes = {}
    for metric in metrics:
        measure = measure_of(metric, (catalog.get(metric) or {}).get("label") or "")
        if not measure:
            continue
        direction = {True: "higher is better", False: "lower is better", None: "neither higher nor lower is better"}
        notes[metric] = f"{measure['label']}: {measure['definition']} ({direction[measure['higher_is_better']]})"
    return notes
