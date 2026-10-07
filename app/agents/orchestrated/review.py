"""Code review of the written answer: every number must come from the computed facts.

The scorer's normalization is reused, so typographic characters can't hide a number.
"""

from __future__ import annotations

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
