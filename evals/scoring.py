"""Score an agent's free-text answer against a question's checks.

Check types:
    {"number": 61948}                       a number within rel_tol must appear
    {"number_any": [0.9849, 98.49]}         any one of these numbers must appear
    {"text_any": ["philadelphia"]}          any one of these strings must appear (case-insensitive)
Optional "rel_tol" on number checks (default 0.001, i.e. 0.1%).

Answers are normalized first: models often write typographic characters (non-breaking
spaces inside "Los Angeles", non-breaking hyphens in "pre-tax", a true minus sign), which
would otherwise make correct answers fail plain-text matching.
"""

from __future__ import annotations

import math
import re
import unicodedata

NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")  # magnitudes only: "fell by -1,619" matches 1619
DEFAULT_REL_TOL = 0.001


def normalize(text: str) -> str:
    """Map every Unicode space to " " and every dash or minus sign to "-"."""
    text = unicodedata.normalize("NFKC", text)
    out = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Zs":
            out.append(" ")
        elif category == "Pd" or char == "\u2212":
            out.append("-")
        else:
            out.append(char)
    return "".join(out)


def numbers_in(text: str) -> list[float]:
    return [float(match.replace(",", "")) for match in NUMBER.findall(normalize(text))]


def _has_number(found: list[float], target: float, rel_tol: float) -> bool:
    return any(math.isclose(value, target, rel_tol=rel_tol) for value in found)


def check_passes(answer: str, check: dict) -> bool:
    rel_tol = check.get("rel_tol", DEFAULT_REL_TOL)
    if "number" in check:
        return _has_number(numbers_in(answer), check["number"], rel_tol)
    if "number_any" in check:
        found = numbers_in(answer)
        return any(_has_number(found, target, rel_tol) for target in check["number_any"])
    if "text_any" in check:
        cleaned = normalize(answer).casefold()
        return any(normalize(option).casefold() in cleaned for option in check["text_any"])
    raise ValueError(f"Unknown check: {check}")


def score(answer: str, checks: list[dict]) -> tuple[bool, list[dict]]:
    """Return (passed, failed_checks). A question passes only if every check passes."""
    failed = [check for check in checks if not check_passes(answer, check)]
    return not failed, failed
