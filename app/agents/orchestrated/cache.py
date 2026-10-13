"""Reuse the model's answers during development, so repeating a question costs no tokens.

Groq's free tier allows 200K tokens a day; a question costs about 4,500. With LLM_CACHE=1 every
successful plan and written answer is stored on disk, keyed by the model name and the exact
messages sent, and returned from disk the next time the same messages come up.

Evals never use this: repeated runs exist to measure how much the model varies, and a cache
would turn three runs into one run counted three times.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app.agents.orchestrated.plan import Plan

DEFAULT_DIR = Path(__file__).resolve().parents[3] / ".llm_cache"


def cache_dir() -> Path | None:
    """Where answers are cached, or None when caching is off (LLM_CACHE unset, empty or 0)."""
    value = os.environ.get("LLM_CACHE", "").strip()
    if value in ("", "0"):
        return None
    return DEFAULT_DIR if value == "1" else Path(value)


def _entry(directory: Path, kind: str, model_name: str, messages: list) -> Path:
    payload = json.dumps([kind, model_name, [[m.type, m.content] for m in messages]])
    return directory / f"{kind}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}.json"


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def cached_plan_fn(plan_fn, model_name: str, directory: Path):
    async def cached(messages: list):
        path = _entry(directory, "plan", model_name, messages)
        if path.exists():
            return Plan.model_validate(json.loads(path.read_text(encoding="utf-8"))), None, {}
        parsed, error, usage = await plan_fn(messages)
        if parsed is not None:  # a failed attempt is retried, never replayed
            _save(path, parsed.model_dump())
        return parsed, error, usage

    return cached


def cached_write_fn(write_fn, model_name: str, directory: Path):
    async def cached(messages: list):
        path = _entry(directory, "write", model_name, messages)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["text"], {}
        text, usage = await write_fn(messages)
        _save(path, {"text": text})
        return text, usage

    return cached
