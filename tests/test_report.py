"""Unit tests for eval aggregation: pure logic, no database or model needed."""

import json

from evals.report import aggregate, load_records


def run(qid, repeat, passed, category="multi_step", error=None):
    return {"id": qid, "repeat": repeat, "passed": passed, "category": category, "error": error,
            "latency_s": 1.0, "input_tokens": 100, "output_tokens": 10}


def test_pass_at_1_and_all_runs_pass():
    records = [run("q1", 0, True), run("q1", 1, True), run("q2", 0, True), run("q2", 1, False)]
    overall = aggregate(records)["overall"]
    assert overall["pass_at_1"] == 0.75   # 3 of 4 runs passed
    assert overall["pass_all"] == 0.5     # only q1 passed every run


def test_latest_record_wins_for_a_resumed_run(tmp_path):
    path = tmp_path / "results.jsonl"
    lines = [run("q1", 0, False, error="429 rate limit"), run("q1", 0, True)]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    records = load_records(path)
    assert len(records) == 1 and records[0]["passed"]


def test_results_are_split_by_category():
    records = [run("q1", 0, True, "single_step"), run("a1", 0, False, "ambiguity")]
    by_category = aggregate(records)["by_category"]
    assert by_category["single_step"]["pass_at_1"] == 1.0
    assert by_category["ambiguity"]["pass_at_1"] == 0.0


def test_rate_limited_runs_are_pending_not_failures():
    records = [run("q1", 0, True), run("q1", 1, False, error="RateLimitError: Error code: 429"),
               run("q2", 0, False, error="GraphRecursionError: Recursion limit of 20 reached")]
    summary = aggregate(records)
    assert summary["pending"] == 1
    assert summary["errors"] == 1                    # the recursion error is the agent's failure
    assert summary["overall"]["pass_at_1"] == 0.5    # q1 run 0 passed, q2 run 0 failed
