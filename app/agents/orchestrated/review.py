"""Code review of the written answer: every number must come from the computed facts, and sit next to
the group and period it belongs to.

The scorer's normalization is reused, so typographic characters can't hide a number.
"""

from __future__ import annotations

import calendar
import datetime as dt
import math
import re

from app.text import normalize

NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    return [float(match.replace(",", "")) for match in NUMBER.findall(normalize(text))]


def supported_numbers(facts: object) -> list[float]:
    """Every number in the facts, plus the roundings and percentage forms a writer may use."""
    found: list[float] = []

    def walk(value):
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            magnitude = abs(float(value))
            found.extend(round(magnitude, digits) for digits in (0, 1, 2))
            found.append(magnitude)
            if magnitude <= 1.5:  # a fraction may be written as a percentage
                found.extend(round(magnitude * 100, digits) for digits in (0, 1, 2))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            found.extend(numbers_in(value))

    walk(facts)
    return found


# ---- which group and period a number belongs to ---------------------------------------------------

MONTHS = [m.lower() for m in calendar.month_name[1:]]
WHOLE_RANGE = re.compile(r"\b(all[- ]time|overall|in all|in total|altogether|all dates|whole|entire|since)\b")


def _labels(value: object, column: str) -> dict[str, str]:
    """How a row's value may be written in an answer, each form mapped to the value it names:
    "brooklyn" -> "brooklyn"; "march 2025", "mar 2025", "2025-03" and "march" -> "2025-03"."""
    if not isinstance(value, str) or not value.strip() or re.fullmatch(r"[\d.,-]+", value):
        return {}
    date = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if date:
        if not column.endswith("__month"):
            return {value[:10]: value[:10]}
        year, month, key = date[1], MONTHS[int(date[2]) - 1], f"{date[1]}-{date[2]}"
        return {f"{month} {year}": key, f"{month[:3]} {year}": key, key: key, month: key}
    name = normalize(value).casefold()
    return {name: name} if len(value.strip()) >= 3 else {}


def _period_labels(period: object) -> dict[str, str]:
    """A step that covers exactly one calendar month is named by that month."""
    if isinstance(period, (list, tuple)):  # [start, end], as older runs recorded it
        period = " to ".join(str(p) for p in period) if all(period) else ""
    found = re.fullmatch(r"(\d{4})-(\d{2})-01 to (\d{4})-(\d{2})-(\d{2})", period or "")
    if not found or found[1] != found[3] or found[2] != found[4]:
        return {}
    year, month = int(found[1]), int(found[2])
    if int(found[5]) != calendar.monthrange(year, month)[1]:
        return {}
    return _labels(dt.date(year, month, 1).isoformat(), "x__month")


def _fact_rows(facts: dict) -> tuple[list[tuple[set[str], set[str], list[float], dict]], dict, dict]:
    """Every result row as (the groups it is about, its period, its numbers, the row), plus the written
    forms of every group and every period."""
    rows, group_forms, time_forms = [], {}, {}
    for step in facts.get("steps", []):
        period = _period_labels(step.get("period", ""))
        for row in step.get("rows", []):
            groups, times = set(), set(period.values())
            time_forms.update(period)
            for column, value in row.items():
                forms = _labels(value, column)
                if re.match(r"\d{4}-\d{2}", str(value)):
                    times.update(forms.values())
                    time_forms.update(forms)
                else:
                    groups.update(forms.values())
                    group_forms.update(forms)
            numbers = supported_numbers({k: v for k, v in row.items() if not isinstance(v, str)})
            rows.append((groups, times, numbers, row))
    # A comparison row (before, after, change) belongs to its group too; it spans two periods, so it has none.
    for comparison in facts.get("comparisons", []):
        for row in comparison.get("rows", []):
            groups = set()
            for column, value in row.items():
                if column != "metric":
                    forms = _labels(value, column)
                    groups.update(forms.values())
                    group_forms.update(forms)
            numbers = supported_numbers({k: v for k, v in row.items() if not isinstance(v, str)})
            rows.append((groups, set(), numbers, row))
    return rows, group_forms, time_forms


def _segments(draft: str) -> list[tuple[str, str]]:
    """(text, extra context) pieces: prose split into clauses; a table cell carries its column's header."""
    pieces, header = [], None
    for line in normalize(draft).casefold().splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if header is None:
                header = cells
                continue
            row_names = " ".join(c for c in cells if not NUMBER.search(c))
            for index, cell in enumerate(cells):
                if NUMBER.search(cell):
                    pieces.append((f"{row_names} {cell}", header[index] if index < len(header) else ""))
            continue
        header = None
        # "X versus Y at Brooklyn": X is the other group's number, so a comparison word ends a clause too.
        clauses = re.split(r"[.;:]\s+|,\s+|\s+-\s+|\(|\)|\s(?:versus|vs\.?|compared (?:with|to)|against|than)\s",
                           stripped)
        pieces.extend((clause, "") for clause in clauses if clause)
    return pieces


def _named(text: str, forms: dict[str, str]) -> set[str]:
    return {value for form, value in forms.items() if re.search(rf"(?<!\w){re.escape(form)}(?!\w)", text)}


def misattributed(draft: str, facts: dict, question: str) -> list[dict]:
    """Numbers placed next to a group or period whose own row says something else.

    Each clause (or table cell, with its column header) is read for the groups and periods it names. When the
    facts hold rows for exactly those names, a number in that clause must come from the most specific of them;
    the m06 miss gave Brooklyn's all-time 493 as its March number, while the facts said 255 for March.
    A clause naming two groups or two periods is left alone (no single row is meant), and "in all" or
    "overall" means the whole range, so only rows without a period count there.
    """
    rows, group_forms, time_forms = _fact_rows(facts)
    unattached = supported_numbers({k: v for k, v in facts.items() if k not in ("steps", "comparisons")}) + \
        supported_numbers([{k: v for k, v in s.items() if k != "rows"} for s in facts.get("steps", [])])
    asked = set(numbers_in(question))
    found = []
    for text, column in _segments(draft):
        context = f"{text} {column}"
        groups, times = _named(context, group_forms), _named(context, time_forms)
        if len(groups) != 1 or len(times) > 1:
            continue
        whole_range = bool(WHOLE_RANGE.search(context))
        if whole_range:
            times = set()
        exact = [r for r in rows if r[0] and r[0] <= groups and r[1] <= times and not (whole_range and r[1])]
        if not exact:
            continue
        depth = max(len(r[0]) + len(r[1]) for r in exact)
        best = [r for r in exact if len(r[0]) + len(r[1]) == depth]
        for number in numbers_in(text):
            whole = number == int(number)
            if (whole and (number <= 31 or 1900 <= number <= 2100)) or number in asked:
                continue

            def close(values: list[float], number: float = number) -> bool:
                return any(math.isclose(number, v, rel_tol=0.0015, abs_tol=0.006) for v in values)

            if any(close(r[2]) for r in best) or close(unattached):
                continue
            elsewhere = [r for r in rows if close(r[2])]
            if elsewhere:
                found.append({"number": number, "named": sorted(groups | times), "right": best[0][3],
                              "belongs_to": elsewhere[0][3]})
    return found


def review_answer(draft: str, facts: dict, question: str, ambiguities: list[dict], catalog: dict) -> list[str]:
    allowed = supported_numbers(facts)
    from_question = set(numbers_in(question))
    unsupported = []
    for number in numbers_in(draft):
        whole = number == int(number)
        if (whole and (number <= 31 or 1900 <= number <= 2100)) or number in from_question:
            continue  # day numbers, small counts, years, numbers the user gave
        if not any(math.isclose(number, value, rel_tol=0.0015, abs_tol=0.006) for value in allowed):
            unsupported.append(number)
    issues = []
    if unsupported:
        issues.append(f"These numbers do not appear in the facts: {unsupported}. Use only numbers from the facts.")
    for item in misattributed(draft, facts, question):
        issues.append(f"{item['number']:g} is written next to {', '.join(item['named'])}, but the facts for that are "
                      f"{_row_text(item['right'])}; {item['number']:g} belongs to {_row_text(item['belongs_to'])}. "
                      "Put each number next to the group and period of its own row.")
    text = normalize(draft).casefold()
    for ambiguity in ambiguities:
        for metric in ambiguity["metrics"]:
            label = catalog[metric]["label"]
            if normalize(label).casefold() not in text and metric not in text:
                issues.append(
                    f"'{ambiguity['term']}' has several definitions: also report {label} ({metric}), "
                    "and say which definition each number uses."
                )
    return issues


def _row_text(row: dict) -> str:
    return "{" + ", ".join(f"{k}: {v}" for k, v in row.items()) + "}"
