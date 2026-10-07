"""Run an agent on an eval set, several times per question, and record every run.

    python -m evals.run_eval --agent orchestrated --set v2 --repeats 3
    python -m evals.run_eval --agent baseline --set v1 --repeats 1
    python -m evals.run_eval --agent orchestrated --set v2 --ids m01,a01 --repeats 1
    python -m evals.run_eval --resume evals/results/<file>.jsonl

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

from app.agents.baseline import DEFAULT_MODEL
from evals.agents import AGENT_VERSIONS, AGENTS
from evals.report import is_rate_limit, print_report
from evals.scoring import score

EVALS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVALS_DIR / "results"
SETS = {"v1": ["baseline_questions.jsonl"], "v2": ["questions_v2.jsonl"], "v3": ["questions_v3_heldout.jsonl"]}
SETS["all"] = SETS["v1"] + SETS["v2"]
STOP_AFTER_RATE_LIMITS = 2


def load_questions(set_name: str, ids: set[str] | None) -> list[dict]:
    questions = []
    for filename in SETS[set_name]:
        for line in (EVALS_DIR / filename).read_text(encoding="utf-8").splitlines():
            if line.strip():
                questions.append(json.loads(line))
    return [q for q in questions if not ids or q["id"] in ids]


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


def parse_args(default_agent: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", choices=sorted(AGENTS), default=default_agent)
    parser.add_argument("--set", choices=sorted(SETS), default="v2")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--ids", help="comma-separated question ids to run")
    parser.add_argument("--resume", type=Path, help="results file to continue")
    return parser.parse_args()


async def main(default_agent: str = "orchestrated") -> None:
    args = parse_args(default_agent)
    load_dotenv(EVALS_DIR.parent / ".env")
    model_name = os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
    pause = float(os.environ.get("EVAL_PAUSE_SECONDS", "3"))
    ids = set(args.ids.split(",")) if args.ids else None

    if args.resume:
        path = args.resume
        first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        set_name, repeats = first["set"], first["repeats"]
        ids = set(first["ids"]) if first.get("ids") else None
        agent_name = first.get("agent", "baseline")
    else:
        set_name, repeats, agent_name = args.set, args.repeats, args.agent
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
        slug = re.sub(r"[^a-z0-9]+", "-", model_name.lower())
        RESULTS_DIR.mkdir(exist_ok=True)
        path = RESULTS_DIR / f"{set_name}_{agent_name}_{slug}_{stamp}.jsonl"

    questions = load_questions(set_name, ids)
    done = finished_runs(path)
    # Repeat-major order: every question gets one run before any gets a second.
    plan = [(q, r) for r in range(repeats) for q in questions if (q["id"], r) not in done]
    print(f"Agent: {agent_name}")
    print(f"{len(plan)} runs to do ({len(questions)} questions x {repeats} repeats, {len(done)} already done)")
    print(f"Results: {path}\n")

    rate_limited_in_a_row = 0
    async with AGENTS[agent_name]() as ask:
        for index, (item, repeat) in enumerate(plan):
            if index:
                await asyncio.sleep(pause)
            started = time.perf_counter()
            try:
                output, error = await ask(item["question"]), None
            except Exception as exc:  # noqa: BLE001 - a failed run fails the question, not the whole eval
                output, error = {"answer": ""}, f"{type(exc).__name__}: {exc}"
            answer = output.pop("answer")
            latency = time.perf_counter() - started
            passed, failed = score(answer, item["checks"])
            record = {
                "set": set_name, "repeats": repeats, "ids": sorted(ids) if ids else None,
                "agent": agent_name, "model": model_name, "harness": AGENT_VERSIONS[agent_name],
                "id": item["id"], "repeat": repeat, "category": item.get("category"),
                "question": item["question"], "passed": passed, "failed_checks": failed,
                "answer": answer, "error": error, "latency_s": round(latency, 2),
                "tool_calls": [], "llm_calls": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0,
                **output,
            }
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record) + "\n")
            mark = "PASS" if passed else ("ERROR" if error else "FAIL")
            print(f"{item['id']} run {repeat + 1}/{repeats} {mark:<5} {latency:5.1f}s tools={len(record['tool_calls'])}")

            rate_limited_in_a_row = rate_limited_in_a_row + 1 if is_rate_limit(error) else 0
            if rate_limited_in_a_row >= STOP_AFTER_RATE_LIMITS:
                print("\nStopped: the model provider keeps rate-limiting (likely the daily token cap).")
                print(f"Resume later with:  python -m evals.run_eval --resume {path}")
                break

    print_report(path)


if __name__ == "__main__":
    asyncio.run(main())
