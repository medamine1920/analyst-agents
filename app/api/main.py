"""Analyst Console API: metrics, evaluation results, saved traces, and live questions streamed step by step.

    uvicorn app.api.main:app --port 8001
"""

from __future__ import annotations

import json
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
from app.api.trace import TRACES_DIR, save_trace, stream_trace

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "evals" / "results"
WEB_DIST = REPO_ROOT / "web" / "dist"


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


def create_app(mcp_target: Any = None, plan_fn=None, write_fn=None,
               results_dir: Path = RESULTS_DIR, traces_dir: Path = TRACES_DIR) -> FastAPI:
    """Build the API. Tests pass an in-process MCP server and scripted planner/writer."""
    load_dotenv(REPO_ROOT / ".env")
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with Client(mcp_target or default_target()) as client:
            state["gateway"] = McpGateway(client)
            yield

    app = FastAPI(title="Analyst Console API", lifespan=lifespan)

    def graph():
        if "graph" not in state:
            from app.agents.orchestrated import graph as orchestrated

            if plan_fn and write_fn:
                planner, writer = plan_fn, write_fn
            else:  # the real model is only created when a live question is asked
                model = orchestrated.groq_model()
                planner, writer = orchestrated.make_plan_fn(model), orchestrated.make_write_fn(model)
            state["graph"] = orchestrated.build_graph(state["gateway"], planner, writer)
        return state["graph"]

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
        async def events():
            collected = []
            try:
                async for event in stream_trace(graph(), request.question):
                    collected.append(event)
                    yield f"data: {json.dumps(event)}\n\n"
                if collected and collected[-1]["type"] == "done":
                    trace_id = save_trace(request.question, collected, traces_dir)
                    yield f"data: {json.dumps({'type': 'saved', 'id': trace_id})}\n\n"
            except Exception as exc:  # noqa: BLE001 - report any failure to the console, never hang the stream
                yield f"data: {json.dumps({'type': 'error', 'message': f'{type(exc).__name__}: {exc}'})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    if WEB_DIST.exists():  # production: the built console is served by the same process
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()
