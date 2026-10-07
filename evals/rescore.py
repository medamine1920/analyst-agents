"""Re-grade saved answers with the current scorer and question checks, without calling the model.

    python -m evals.rescore evals/results/<file>.jsonl

Use it after fixing the scorer or a golden answer: every run that produced an answer is
graded again and the file is rewritten in place. Runs that errored are left untouched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from evals.report import print_report
from evals.scoring import score

EVALS_DIR = Path(__file__).resolve().parent
QUESTION_FILES = ["baseline_questions.jsonl", "questions_v2.jsonl", "questions_v3_heldout.jsonl"]


def load_checks() -> dict[str, list[dict]]:
    checks = {}
    for filename in QUESTION_FILES:
        for line in (EVALS_DIR / filename).read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                checks[item["id"]] = item["checks"]
    return checks


def rescore(path: Path) -> int:
    checks = load_checks()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    changed = 0
    for record in lines:
        if record.get("error"):
            continue
        passed, failed = score(record["answer"], checks[record["id"]])
        if passed != record["passed"]:
            changed += 1
            print(f"{record['id']} run {record['repeat'] + 1}: {'FAIL -> PASS' if passed else 'PASS -> FAIL'}")
        record["passed"], record["failed_checks"] = passed, failed
    path.write_text("".join(json.dumps(record) + "\n" for record in lines), encoding="utf-8")
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m evals.rescore <results.jsonl>")
    results = Path(sys.argv[1])
    print(f"{rescore(results)} run(s) changed verdict")
    print_report(results)
