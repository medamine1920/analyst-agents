"""Analyst Console API: metrics, evaluation results, saved traces, and live questions streamed step by step.

    uvicorn app.api.main:app --port 8001

A live question can pause to ask which definition of a business term is meant: /api/ask then ends
with an "approval" event, and /api/resume continues the same run with the answer. Paused runs are
kept on disk (LangGraph checkpoints plus the steps streamed so far), so they survive a restart.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp import Client
from pydantic import BaseModel, Field

from app.agents.mcp_tools import default_target
from app.agents.orchestrated.gateway import McpGateway
from app.agents.orchestrated.graph import (
    open_checkpointer,
    pending_approval,
    thread_config,
)
from app.agents.orchestrated.plan import ALL_DEFINITIONS
from app.api.trace import TRACES_DIR, save_trace, stream_trace

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "evals" / "results"
WEB_DIST = REPO_ROOT / "web" / "dist"


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    interactive: bool = Field(default=False, description="Pause and ask when a business term is ambiguous")


class ResumeRequest(BaseModel):
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    choice: str | dict[str, str] = Field(description='A metric name, or "all"; or one of those per term')


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def create_app(mcp_target: Any = None, plan_fn=None, write_fn=None, results_dir: Path = RESULTS_DIR,
               traces_dir: Path = TRACES_DIR, checkpoint_db: Path | None = None, trace_label: str = "") -> FastAPI:
    """Build the API. Tests pass an in-process MCP server and scripted planner/writer."""
    load_dotenv(REPO_ROOT / ".env")
    state: dict[str, Any] = {}
    paused_dir = traces_dir / "paused"  # the steps already shown for runs waiting on the user

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with Client(mcp_target or default_target()) as client, open_checkpointer(checkpoint_db) as saver:
            state["gateway"], state["checkpointer"] = McpGateway(client), saver
            yield

    app = FastAPI(title="Analyst Console API", lifespan=lifespan)

    def graph():
        if "graph" not in state:
            from app.agents.orchestrated import graph as orchestrated

            if plan_fn and write_fn:
                planner, writer = plan_fn, write_fn
            else:  # the real model is only created when a live question is asked
                planner, writer = orchestrated.model_fns()
            state["graph"] = orchestrated.build_graph(state["gateway"], planner, writer, state["checkpointer"])
        return state["graph"]

    def respond(question: str, thread_id: str, stream, earlier: list[dict]) -> StreamingResponse:
        """Stream a run. When it pauses, keep its steps; when it finishes, save the whole trace."""
        async def events():
            collected = list(earlier)
            paused_file = paused_dir / f"{thread_id}.json"
            try:
                async for event in stream:
                    collected.append(event)
                    yield sse(event)
                if collected[-1]["type"] == "approval":
                    paused_dir.mkdir(parents=True, exist_ok=True)
                    paused_file.write_text(json.dumps({"question": question, "events": collected}), encoding="utf-8")
                elif collected[-1]["type"] == "done":
                    trace_id = save_trace(question, collected, traces_dir, label=trace_label)
                    paused_file.unlink(missing_ok=True)
                    await state["checkpointer"].adelete_thread(thread_id)  # a finished run needs no checkpoint
                    yield sse({"type": "saved", "id": trace_id})
            except Exception as exc:  # noqa: BLE001 - report any failure to the console, never hang the stream
                yield sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/metrics")
    async def metrics() -> dict:
        context = await state["gateway"].context()
        return {"data_range": context["data_range"], "metrics": list(context["catalog"].values())}

    @app.get("/api/evals")
    async def evals() -> list[dict]:
        from evals.report import aggregate, load_records

        runs = []
        for path in sorted(results_dir.glob("*.jsonl")):
            records = load_records(path)
            if not records:
                continue
            first = records[0]
            runs.append({"file": path.name, "agent": first.get("agent"), "model": first.get("model"),
                         "set": first.get("set"), "summary": aggregate(records)})
        return runs

    @app.get("/api/evals/{file_name}/{question_id}")
    async def eval_question(file_name: str, question_id: str) -> list[dict]:
        from evals.report import load_records

        path = results_dir / file_name
        if path.parent != results_dir or not path.exists():
            raise HTTPException(404, "No results file with that name.")
        keep = ("repeat", "passed", "failed_checks", "answer", "error", "latency_s", "input_tokens", "output_tokens")
        return [{k: r.get(k) for k in keep} for r in sorted(load_records(path), key=lambda r: r["repeat"])
                if r["id"] == question_id]

    @app.get("/api/traces")
    async def traces() -> list[dict]:
        found = []
        for path in sorted(traces_dir.glob("*.json"), reverse=True):
            data = json.loads(path.read_text(encoding="utf-8"))
            found.append({k: data.get(k) for k in ("id", "question", "label", "created")})
        return found

    @app.get("/api/traces/{trace_id}")
    async def trace(trace_id: str) -> dict:
        path = traces_dir / f"{trace_id}.json"
        if path.parent != traces_dir or not path.exists():
            raise HTTPException(404, "No saved run with that id.")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/api/ask")
    async def ask(request: AskRequest) -> StreamingResponse:
        thread_id = uuid.uuid4().hex
        stream = stream_trace(graph(), request.question, thread_id=thread_id, interactive=request.interactive)
        return respond(request.question, thread_id, stream, [])

    @app.post("/api/resume")
    async def resume(request: ResumeRequest) -> StreamingResponse:
        snapshot = await graph().aget_state(thread_config(request.thread_id))
        waiting = pending_approval(snapshot)
        if waiting is None:
            raise HTTPException(404, "No paused question with that id. It may already have been answered.")
        for item in waiting["questions"]:
            allowed = [option["metric"] for option in item["options"]] + [ALL_DEFINITIONS]
            value = request.choice.get(item["term"]) if isinstance(request.choice, dict) else request.choice
            if value not in allowed:
                raise HTTPException(422, f"Choose one of {allowed} for '{item['term']}'.")
        paused_file = paused_dir / f"{request.thread_id}.json"
        saved = json.loads(paused_file.read_text(encoding="utf-8")) if paused_file.exists() else {}
        earlier = saved.get("events", [])
        stream = stream_trace(graph(), "", thread_id=request.thread_id, resume=request.choice,
                              elapsed_before=earlier[-1]["elapsed_s"] if earlier else 0.0)
        return respond(snapshot.values["question"], request.thread_id, stream, earlier)

    if WEB_DIST.exists():  # production: the built console is served by the same process
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()
