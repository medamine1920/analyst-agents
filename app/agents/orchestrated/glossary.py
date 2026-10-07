"""Resolve business terms in a question against the glossary, in code.

The baseline was told in its prompt to report every definition of an ambiguous term,
and it ignored that instruction on every run. Here ambiguity is detected by code before
planning, and enforced again after planning, so the model cannot skip it.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from app.text import normalize

GLOSSARY_PATH = Path(__file__).resolve().parents[2] / "semantic" / "glossary.yml"


@lru_cache(maxsize=1)
def load_glossary() -> list[dict]:
    return yaml.safe_load(GLOSSARY_PATH.read_text(encoding="utf-8"))["entries"]


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", text) is not None


def resolve_terms(question: str) -> dict:
    """Return {"ambiguities": [...], "resolved": [...]} for glossary terms found in the question."""
    text = normalize(question).casefold()
    entries = sorted(load_glossary(), key=lambda e: -max(len(t) for t in e["terms"]))
    ambiguities, resolved = [], []
    for entry in entries:
        found = next((term for term in entry["terms"] if _contains(text, term)), None)
        if not found:
            continue
        text = re.sub(rf"(?<![a-z]){re.escape(found)}(?![a-z])", " ", text)  # longer phrase wins
        metrics = entry["metrics"]
        if len(metrics) == 1:
            resolved.append({"term": found, "metrics": metrics})
            continue
        qualifiers = entry.get("qualifiers", {})
        chosen = [m for m in metrics if any(_contains(text, q) for q in qualifiers.get(m, []))]
        if chosen:
            resolved.append({"term": found, "metrics": chosen})
        else:
            ambiguities.append({"term": found, "metrics": metrics})
    return {"ambiguities": ambiguities, "resolved": resolved}
