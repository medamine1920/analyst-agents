"""Saved answers and question suggestions.

Saved answers: when a live question finishes and its answer passed the number check, the whole run
(every step, so it replays like any trace) is stored in the console database. Asking the same
question again replays it at once and spends no tokens. An answer is reused only while it is
recent (SAVED_ANSWER_DAYS, default 7) and only for the same data and metric definitions: when the
warehouse or the semantic layer changes, every saved answer stops matching.

"The same question" means the same words once case, spacing, typographic characters and final
punctuation are ignored, asked in the same mode (interactive runs may ask which definition is
meant; others report every definition). Runs where the user picked a definition are not reused.

Suggestions come from the audit log (questions people asked before) and the metric catalog.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.text import normalize

SCHEMA = """
create table if not exists saved_answers (
    key text primary key, question text not null, data_version text not null,
    saved_at real not null, trace_id text, events text not null, reused integer not null default 0);
"""


def normalized_question(question: str) -> str:
    text = re.sub(r"\s+", " ", normalize(question).casefold()).strip()
    return text.rstrip("?.! ")


def data_version(context: dict) -> str:
    """Changes whenever the data range or any metric's definition changes."""
    catalog = {name: [info.get("description"), info.get("type"), sorted(info.get("group_by", []))]
               for name, info in context["catalog"].items()}
    payload = json.dumps([context["data_range"], catalog], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def answer_key(question: str, interactive: bool, source: str = "sample") -> str:
    key = f"{'interactive' if interactive else 'all-definitions'}|{normalized_question(question)}"
    return key if source == "sample" else f"{source}|{key}"  # sample keys predate sources: unchanged


def reusable(events: list[dict]) -> bool:
    """Only finished runs whose final check passed, and where nobody had to pick a definition."""
    steps = [e for e in events if e.get("type") == "step"]
    reviews = [s for s in steps if s.get("node") == "review"]
    finished = bool(events) and events[-1].get("type") == "done" and events[-1].get("answer")
    picked = any(s.get("node") == "clarify" for s in steps)
    return bool(finished and reviews and not reviews[-1]["data"].get("issues") and not picked)


class AnswerStore:
    def __init__(self, path: Path, max_age_days: float | None = None) -> None:
        self.path = path
        self.max_age_s = 86400 * float(max_age_days if max_age_days is not None
                                       else os.environ.get("SAVED_ANSWER_DAYS", "7"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, question: str, interactive: bool, version: str, events: list[dict], trace_id: str,
             source: str = "sample") -> bool:
        if not reusable(events):
            return False
        with self._db() as db:
            db.execute("insert or replace into saved_answers (key, question, data_version, saved_at, trace_id, events) "
                       "values (?, ?, ?, ?, ?, ?)",
                       (answer_key(question, interactive, source), question, version, time.time(), trace_id,
                        json.dumps(events)))
        return True

    def find(self, question: str, interactive: bool, version: str, source: str = "sample") -> dict | None:
        """The saved run for this question, if it is recent and was made from the same data."""
        with self._db() as db:
            row = db.execute("select * from saved_answers where key = ? and data_version = ? and saved_at > ?",
                             (answer_key(question, interactive, source), version,
                              time.time() - self.max_age_s)).fetchone()
            if not row:
                return None
            db.execute("update saved_answers set reused = reused + 1 where key = ?", (row["key"],))
        return {"question": row["question"], "saved_at": row["saved_at"], "trace_id": row["trace_id"],
                "events": json.loads(row["events"])}

    def has(self, question: str, version: str, source: str = "sample") -> bool:
        with self._db() as db:
            return db.execute("select 1 from saved_answers where key in (?, ?) and data_version = ? and saved_at > ?",
                              (answer_key(question, False, source), answer_key(question, True, source), version,
                               time.time() - self.max_age_s)).fetchone() is not None


def suggest(text: str, past_questions: list[str], catalog: dict, limit: int = 5) -> dict:
    """Past questions containing every typed word (most asked first), and metrics named by any of them."""
    words = [w for w in re.findall(r"[a-z0-9]+", normalize(text).casefold()) if len(w) > 1]
    counts: Counter[str] = Counter()
    wording: dict[str, str] = {}
    for question in past_questions:  # newest first, so the most recent wording is kept
        key = normalized_question(question)
        counts[key] += 1
        wording.setdefault(key, question)
    matching = [key for key, _ in counts.most_common() if all(w in key for w in words)]
    # Metrics: a typed word of 4+ letters that appears in a metric's name or label ("orders" finds
    # order_count). Most words of a question name no metric, so one match is enough; more rank higher.
    terms = [w for w in words if len(w) >= 4]
    scored = []
    for name, info in catalog.items():
        haystack = f"{name} {info.get('label', '')}".casefold().replace("_", " ")
        hits = sum(1 for w in terms if w in haystack or w.rstrip("s") in haystack)
        if hits:
            scored.append((-hits, name, {"name": name, "label": info.get("label", name),
                                         "description": info.get("description", "")}))
    metrics = [item for _, _, item in sorted(scored)]
    return {"questions": [{"question": wording[key], "times_asked": counts[key]} for key in matching[:limit]],
            "metrics": metrics[:limit]}
