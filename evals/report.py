"""Summarize an eval results file (JSON lines, one record per question run).

    python -m evals.report evals/results/<file>.jsonl

pass@1 : share of all runs that passed (average single-run accuracy).
pass^k : share of questions that passed on every one of their k runs (reliability).

Runs stopped by a provider rate limit are "pending", not failures: they say nothing about
the agent, and resuming the eval reruns them. Every other error (a malformed tool call,
hitting the step limit) is the agent's failure and counts against it.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def is_rate_limit(error: str | None) -> bool:
    return bool(error) and ("429" in error or "rate limit" in error.lower() or "RateLimit" in error)


def load_records(path: Path) -> list[dict]:
    """Read records, keeping only the latest record for each (question, repeat)."""
    latest: dict[tuple[str, int], dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            latest[(record["id"], record["repeat"])] = record
    return list(latest.values())


def aggregate(all_records: list[dict]) -> dict:
    pending = [record for record in all_records if is_rate_limit(record.get("error"))]
    records = [record for record in all_records if not is_rate_limit(record.get("error"))]
    by_question: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_question[record["id"]].append(record)

    def summarize(question_ids: list[str]) -> dict:
        runs = [run for qid in question_ids for run in by_question[qid]]
        if not runs:
            return {"questions": 0, "runs": 0, "pass_at_1": 0.0, "pass_all": 0.0}
        return {
            "questions": len(question_ids),
            "runs": len(runs),
            "pass_at_1": sum(run["passed"] for run in runs) / len(runs),
            "pass_all": sum(all(run["passed"] for run in by_question[qid]) for qid in question_ids)
            / len(question_ids),
        }

    categories: dict[str, list[str]] = defaultdict(list)
    for qid, runs in by_question.items():
        categories[runs[0].get("category", "uncategorized")].append(qid)

    return {
        "overall": summarize(list(by_question)),
        "by_category": {name: summarize(ids) for name, ids in sorted(categories.items())},
        "by_question": {
            qid: f"{sum(run['passed'] for run in runs)}/{len(runs)}" for qid, runs in sorted(by_question.items())
        },
        "errors": sum(1 for record in records if record.get("error")),
        "pending": len(pending),
        "avg_latency_s": sum(record["latency_s"] for record in records) / max(len(records), 1),
        "avg_tokens": sum(record["input_tokens"] + record["output_tokens"] for record in records)
        / max(len(records), 1),
    }


def print_report(path: Path) -> dict:
    records = load_records(path)
    summary = aggregate(records)
    first = records[0]
    overall = summary["overall"]
    print(f"\nAgent: {first.get('agent')}   Model: {first.get('model')}   File: {path.name}")
    print(f"Overall: pass@1 {overall['pass_at_1']:.0%}  |  all-runs pass {overall['pass_all']:.0%}"
          f"  ({overall['questions']} questions, {overall['runs']} runs)")
    for name, stats in summary["by_category"].items():
        print(f"  {name:<12} pass@1 {stats['pass_at_1']:.0%}  all-runs {stats['pass_all']:.0%}  ({stats['questions']} q)")
    print("Per question:", "  ".join(f"{qid} {score}" for qid, score in summary["by_question"].items()))
    print(f"Avg latency {summary['avg_latency_s']:.1f}s  |  avg tokens/run {summary['avg_tokens']:,.0f}"
          f"  |  agent errors {summary['errors']}  |  pending (rate-limited) {summary['pending']}")
    return summary


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m evals.report <results.jsonl>")
    print_report(Path(sys.argv[1]))
