"""Run the baseline agent on an eval set, several times per question, and record every run.

    python -m evals.run_baseline --set v2 --repeats 3
    python -m evals.run_baseline --set v1 --repeats 1
    python -m evals.run_baseline --set v2 --ids m01,a01 --repeats 1
    python -m evals.run_baseline --resume evals/results/<file>.jsonl

Each run is appended to a JSON-lines file as soon as it finishes, so a run stopped by a rate
limit (Groq's free tier allows 200K tokens a day for gpt-oss-120b) can be resumed later.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage

from app.agents.baseline import (
    DEFAULT_MODEL,
    build_agent,
    build_model,
    final_answer,
    run_question,
)
from app.agents.mcp_tools import mcp_tools
from evals.report import is_rate_limit, print_report
from evals.scoring import score

EVALS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVALS_DIR / "results"
SETS = {"v1": ["baseline_questions.jsonl"], "v2": ["questions_v2.jsonl"]}
SETS["all"] = SETS["v1"] + SETS["v2"]
AGENT = "baseline"
STOP_AFTER_RATE_LIMITS = 2


def load_questions(set_name: str, ids: set[str] | None) -> list[dict]:
    questions = []
    for filename in SETS[set_name]:
        for line in (EVALS_DIR / filename).read_text(encoding="utf-8").splitlines():
            if line.strip():
                questions.append(json.loads(line))
    return [q for q in questions if not ids or q["id"] in ids]


def summarize_run(messages: list) -> dict:
    ai_messages = [message for message in messages if isinstance(message, AIMessage)]
    usage = [message.usage_metadata or {} for message in ai_messages]
    return {
        "tool_calls": [call["name"] for message in ai_messages for call in message.tool_calls],
        "llm_calls": len(ai_messages),
        "input_tokens": sum(item.get("input_tokens", 0) for item in usage),
        "cached_tokens": sum((item.get("input_token_details") or {}).get("cache_read", 0) for item in usage),
        "output_tokens": sum(item.get("output_tokens", 0) for item in usage),
    }


def finished_runs(path: Path) -> set[tuple[str, int]]:
    """(question, repeat) pairs already completed without error."""
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if not record.get("error"):
                done.add((record["id"], record["repeat"]))
    return done


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=sorted(SETS), default="v2")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--ids", help="comma-separated question ids to run")
    parser.add_argument("--resume", type=Path, help="results file to continue")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    load_dotenv(EVALS_DIR.parent / ".env")
    model_name = os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
    pause = float(os.environ.get("EVAL_PAUSE_SECONDS", "3"))
    ids = set(args.ids.split(",")) if args.ids else None

    if args.resume:
        path = args.resume
        first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        set_name, repeats = first["set"], first["repeats"]
        ids = set(first["ids"]) if first.get("ids") else None
    else:
        set_name, repeats = args.set, args.repeats
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
        slug = re.sub(r"[^a-z0-9]+", "-", model_name.lower())
        RESULTS_DIR.mkdir(exist_ok=True)
        path = RESULTS_DIR / f"{set_name}_{AGENT}_{slug}_{stamp}.jsonl"

    questions = load_questions(set_name, ids)
    done = finished_runs(path)
    # Repeat-major order: every question gets one run before any gets a second.
    plan = [(q, r) for r in range(repeats) for q in questions if (q["id"], r) not in done]
    print(f"{len(plan)} runs to do ({len(questions)} questions x {repeats} repeats, {len(done)} already done)")
    print(f"Results: {path}\n")

    rate_limited_in_a_row = 0
    async with mcp_tools() as tools:
        agent = build_agent(build_model(), tools)
        for index, (item, repeat) in enumerate(plan):
            if index:
                await asyncio.sleep(pause)
            started = time.perf_counter()
            try:
                messages = await run_question(agent, item["question"])
                answer, error = final_answer(messages), None
            except Exception as exc:  # noqa: BLE001 - a failed run fails the question, not the whole eval
                messages, answer, error = [], "", f"{type(exc).__name__}: {exc}"
            latency = time.perf_counter() - started
            passed, failed = score(answer, item["checks"])
            record = {
                "set": set_name, "repeats": repeats, "ids": sorted(ids) if ids else None,
                "agent": AGENT, "model": model_name,
                "id": item["id"], "repeat": repeat, "category": item.get("category"),
                "question": item["question"], "passed": passed, "failed_checks": failed,
                "answer": answer, "error": error, "latency_s": round(latency, 2),
                **summarize_run(messages),
            }
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record) + "\n")
            mark = "PASS" if passed else ("ERROR" if error else "FAIL")
            print(f"{item['id']} run {repeat + 1}/{repeats} {mark:<5} {latency:5.1f}s tools={len(record['tool_calls'])}")

            rate_limited_in_a_row = rate_limited_in_a_row + 1 if is_rate_limit(error) else 0
            if rate_limited_in_a_row >= STOP_AFTER_RATE_LIMITS:
                print("\nStopped: the model provider keeps rate-limiting (likely the daily token cap).")
                print(f"Resume later with:  python -m evals.run_baseline --resume {path}")
                break

    print_report(path)


if __name__ == "__main__":
    asyncio.run(main())
