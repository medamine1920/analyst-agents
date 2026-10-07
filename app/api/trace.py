"""Run the orchestrated graph and turn each node's update into a readable, replayable trace."""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TRACES_DIR = Path(__file__).resolve().parents[2] / "traces"


def jsonable(value: Any) -> Any:
    """Dates, decimals and other objects become plain JSON values."""
    return json.loads(json.dumps(value, default=str))


def usage_totals(usages: list[dict]) -> dict:
    return {
        "llm_calls": len(usages),
        "input_tokens": sum(u.get("input_tokens", 0) for u in usages),
        "output_tokens": sum(u.get("output_tokens", 0) for u in usages),
    }


def summarize(node: str, update: dict) -> dict:
    """Keep what a person needs to see for each step, nothing more."""
    if node == "resolve":
        return {"resolution": update.get("resolution", {})}
    if node == "plan":
        return {"plan": update.get("plan"), "attempts": update.get("plan_attempts"),
                "ambiguities": update.get("ambiguities", []), "error": update.get("error")}
    if node == "execute":
        results = update.get("results", {})
        return {"queries": {step: {"columns": r.get("columns", []), "rows": r.get("row_count", 0)}
                            for step, r in results.items()},
                "errors": update.get("feedback", [])}
    if node in ("analyze", "analyze_partial"):
        return {"facts": update.get("facts", {}), "partial": node == "analyze_partial"}
    if node == "write":
        return {"draft": update.get("draft", ""), "attempt": update.get("write_attempts")}
    if node == "review":
        return {"issues": update.get("review_issues", []), "final": "answer" in update}
    if node == "give_up":
        return {"answer": update.get("answer", "")}
    return update


async def stream_trace(graph: Any, question: str) -> AsyncIterator[dict]:
    """Yield one event per graph step, then a final summary event."""
    started = time.perf_counter()
    usage: list[dict] = []
    answer = ""
    yield {"type": "start", "question": question}
    async for chunk in graph.astream({"question": question}, stream_mode="updates"):
        for node, update in chunk.items():
            update = update or {}
            usage = update.get("usage", usage)
            answer = update.get("answer", answer)
            yield {"type": "step", "node": node, "elapsed_s": round(time.perf_counter() - started, 2),
                   "data": jsonable(summarize(node, update))}
    yield {"type": "done", "answer": answer, "elapsed_s": round(time.perf_counter() - started, 2),
           "usage": usage_totals(usage)}


def save_trace(question: str, events: list[dict], directory: Path = TRACES_DIR, label: str = "",
               prefix: str = "") -> str:
    """Store a finished run so the console can replay it without calling the model."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:48]
    trace_id = f"{prefix}{stamp}-{slug}"
    payload = {"id": trace_id, "question": question, "label": label, "created": stamp, "events": events}
    (directory / f"{trace_id}.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return trace_id
