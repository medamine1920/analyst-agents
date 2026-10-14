"""Analyst Console API: metrics, evaluation results, saved traces, and live questions streamed step by step.

    uvicorn app.api.main:app --port 8001

A live question can pause to ask which definition of a business term is meant: /api/ask then ends
with an "approval" event, and /api/resume continues the same run with the answer. Paused runs are
kept on disk (LangGraph checkpoints plus the steps streamed so far), so they survive a restart.

Every route except /api/health and sign-in needs a signed-in user (see security.py); CONSOLE_AUTH=off
turns that off for local development. Live questions also stop when the day's token budget is spent.

Other applications connect with an API key (see connectors.py): POST /api/v1/ask for one JSON
result, or the MCP endpoint /mcp. A key acts as its owner but cannot create or revoke keys.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp import Client
from pydantic import BaseModel, Field
from starlette.routing import Route

from app.agents.mcp_tools import default_target
from app.agents.orchestrated.gateway import McpGateway
from app.agents.orchestrated.graph import (
    open_checkpointer,
    pending_approval,
    thread_config,
)
from app.agents.orchestrated.plan import ALL_DEFINITIONS
from app.api.answers import AnswerStore, data_version, suggest
from app.api.connectors import REQUEST_USER, build_mcp_app, result_from_events
from app.api.security import (
    SESSION_COOKIE,
    SESSION_HOURS,
    LimitReached,
    Limits,
    SecurityStore,
)
from app.api.trace import TRACES_DIR, save_trace, stream_trace

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "evals" / "results"
WEB_DIST = REPO_ROOT / "web" / "dist"
# Groq's free tier allows 8K tokens a minute, so a few runs at once already hit the limit: the rest wait.
MAX_LIVE_RUNS = int(os.environ.get("MAX_LIVE_RUNS", "2"))
OPEN_PATHS = {"/api/health", "/api/auth/login", "/api/auth/me"}


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    interactive: bool = Field(default=False, description="Pause and ask when a business term is ambiguous")
    fresh: bool = Field(default=False, description="Run the agent even if a saved answer exists")


class ConnectorAskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    fresh: bool = Field(default=False, description="Run the agent even if a saved answer exists")


class KeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80, description="What the key is for, e.g. 'Sales sheet'")


class SignInRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class ResumeRequest(BaseModel):
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    choice: str | dict[str, str] = Field(description='A metric name, or "all"; or one of those per term')


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def create_app(mcp_target: Any = None, plan_fn=None, write_fn=None, results_dir: Path = RESULTS_DIR,
               traces_dir: Path = TRACES_DIR, checkpoint_db: Path | None = None, trace_label: str = "",
               auth: bool | None = None, security_db: Path | None = None, limits: Limits | None = None,
               live_traces_dir: Path | None = None) -> FastAPI:
    """Build the API. Tests pass an in-process MCP server and scripted planner/writer.

    auth=None reads CONSOLE_AUTH: sign-in is required unless it is "off".
    Live runs are saved in live_traces_dir (LIVE_TRACES_DIR; on the server a folder in the volume, so
    they survive deploys); traces_dir holds the demo runs that ship with the code. Both are listed.
    """
    load_dotenv(REPO_ROOT / ".env")
    state: dict[str, Any] = {}
    auth = (os.environ.get("CONSOLE_AUTH", "on").lower() != "off") if auth is None else auth
    store, limits = SecurityStore(security_db), limits or Limits.from_env()
    answers = AnswerStore(store.path)  # same database file: it is the volume that survives deploys
    live_runs = asyncio.Semaphore(MAX_LIVE_RUNS)
    live_dir = Path(live_traces_dir or os.environ.get("LIVE_TRACES_DIR") or traces_dir)
    trace_dirs = list(dict.fromkeys([live_dir, traces_dir]))  # live runs first; the same folder only once
    paused_dir = live_dir / "paused"  # the steps already shown for runs waiting on the user

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The MCP endpoint's session manager runs once per instance, so each start builds a new one.
        state["mcp_http"] = mcp_http = build_mcp_app(answer_once, catalog)
        async with (Client(mcp_target or default_target()) as client, open_checkpointer(checkpoint_db) as saver,
                    mcp_http.router.lifespan_context(mcp_http)):
            state["gateway"], state["checkpointer"] = McpGateway(client), saver
            yield

    app = FastAPI(title="Analyst Console API", lifespan=lifespan)

    @app.middleware("http")
    async def require_sign_in(request: Request, call_next):
        """Every API route needs a signed-in user or an API key, so a new route cannot be left open.

        Browsers sign in with the session cookie. Other applications send an API key as a Bearer token;
        /mcp accepts only keys.
        """
        path = request.url.path
        header = request.headers.get("authorization", "")
        key = header[7:].strip() if header.lower().startswith("bearer ") else None
        key_user = store.user_for_key(key) if key and auth else None
        if key and auth and not key_user:
            return JSONResponse({"detail": "That API key is not valid. It may have been revoked."}, status_code=401)
        if not auth:
            user, via = "local", "local"
        elif key_user:
            user, via = key_user, "key"
        else:
            user, via = store.user_for(request.cookies.get(SESSION_COOKIE)), "session"
        request.state.user, request.state.via = user, via
        if path == "/mcp" and auth and via != "key":
            return JSONResponse({"detail": "Send an API key as 'Authorization: Bearer ak_...'."}, status_code=401)
        if path.startswith("/api/") and path not in OPEN_PATHS and not user:
            return JSONResponse({"detail": "Sign in to use the console."}, status_code=401)
        token = REQUEST_USER.set(user)
        try:
            return await call_next(request)
        finally:
            REQUEST_USER.reset(token)

    @app.post("/api/auth/login")
    async def sign_in(body: SignInRequest, request: Request, response: Response) -> dict:
        try:
            token = store.sign_in(body.name, body.password)
        except LimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
        if token is None:
            raise HTTPException(401, "Wrong name or password.")
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True,
                            samesite="lax", secure=https, path="/")
        return {"user": body.name}

    @app.post("/api/auth/logout")
    async def sign_out(request: Request, response: Response) -> dict:
        store.sign_out(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"user": None}

    @app.get("/api/auth/me")
    async def me(request: Request) -> dict:
        """Who is signed in (null if nobody), and how much of today's budget is left."""
        user = request.state.user
        return {"user": user, "auth": auth, "usage": store.usage(user, limits) if user else None}

    async def current_data_version() -> str:
        return data_version(await state["gateway"].context())

    @app.get("/api/suggestions")
    async def suggestions(q: str = "") -> dict:
        """Questions asked before (most asked first) and metrics matching what is being typed."""
        context, version = await state["gateway"].context(), await current_data_version()
        found = suggest(q, store.recent_questions(), context["catalog"])
        for item in found["questions"]:
            item["saved_answer"] = answers.has(item["question"], version)
        return found

    @app.get("/api/history")
    async def history(request: Request) -> list[dict]:
        """The signed-in user's recent questions, newest first."""
        return store.history(request.state.user, limit=50)

    def graph():
        if "graph" not in state:
            from app.agents.orchestrated import graph as orchestrated

            if plan_fn and write_fn:
                planner, writer = plan_fn, write_fn
            else:  # the real model is only created when a live question is asked
                planner, writer = orchestrated.model_fns()
            state["graph"] = orchestrated.build_graph(state["gateway"], planner, writer, state["checkpointer"])
        return state["graph"]

    def spent(event: dict) -> int:
        usage = event.get("usage") or {}
        return usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

    async def run_events(question: str, thread_id: str, stream, earlier: list[dict], question_id: int | None,
                         interactive: bool = True):
        """Follow a run. When it pauses, keep its steps; when it finishes, save the whole trace.

        The audit log follows the run: waiting, answered or failed, with the tokens it spent.
        """
        collected = list(earlier)
        paused_file = paused_dir / f"{thread_id}.json"
        try:
            async with live_runs:
                async for event in stream:
                    collected.append(event)
                    yield event
            last = collected[-1]
            if last["type"] == "approval":
                paused_dir.mkdir(parents=True, exist_ok=True)
                paused_file.write_text(json.dumps({"question": question, "events": collected}), encoding="utf-8")
                if question_id:
                    store.finish_question(question_id, "waiting", spent(last))
            elif last["type"] == "done":
                trace_id = save_trace(question, collected, live_dir, label=trace_label)
                paused_file.unlink(missing_ok=True)
                await state["checkpointer"].adelete_thread(thread_id)  # a finished run needs no checkpoint
                if question_id:
                    store.finish_question(question_id, "answered", spent(last), trace_id)
                answers.save(question, interactive, await current_data_version(), collected, trace_id)
                yield {"type": "saved", "id": trace_id}
        except Exception as exc:  # noqa: BLE001 - report any failure to the caller, never hang
            if question_id:
                store.finish_question(question_id, "failed")
            yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}

    async def saved_events(saved: dict):
        """A saved run, replayed like a live one and saying where it came from. No model call, no tokens."""
        note = {"type": "reused", "saved_at": saved["saved_at"], "trace_id": saved["trace_id"],
                "question": saved["question"]}
        for event in saved["events"]:
            if event["type"] == "done":
                yield note
                event = {**event, "reused": True}
            yield event

    async def ask_events(user: str, question: str, interactive: bool, fresh: bool):
        """The saved answer, or a new logged run. Raises LimitReached before anything is spent.

        Not a generator itself, so the limit check happens when it is called, not when it is read.
        """
        if not fresh:
            saved = answers.find(question, interactive, await current_data_version())
            if saved:
                store.log_reused(user, question, saved["trace_id"])
                return saved_events(saved)
        thread_id = uuid.uuid4().hex
        question_id = store.start_question(user, question, limits, thread_id)
        stream = stream_trace(graph(), question, thread_id=thread_id, interactive=interactive)
        return run_events(question, thread_id, stream, [], question_id, interactive)

    def streamed(events) -> StreamingResponse:
        async def body():
            async for event in events:
                yield sse(event)

        return StreamingResponse(body(), media_type="text/event-stream")

    async def answer_once(user: str, question: str, fresh: bool = False) -> dict:
        """A whole answer as one JSON result, for connectors (never interactive)."""
        events = [event async for event in await ask_events(user, question, False, fresh)]
        return result_from_events(events)

    async def catalog() -> list[dict]:
        return list((await state["gateway"].context())["catalog"].values())

    class McpEndpoint:
        """Hands /mcp requests to the MCP app of the current start (an ASGI app, not a request handler)."""

        async def __call__(self, scope, receive, send):
            await state["mcp_http"](scope, receive, send)

    # A plain route, not a mount: clients POST to /mcp exactly, with no redirect to /mcp/.
    app.router.routes.append(Route("/mcp", endpoint=McpEndpoint(), methods=["GET", "POST", "DELETE"]))

    def browser_only(request: Request) -> None:
        if request.state.via == "key":
            raise HTTPException(403, "API keys cannot manage keys: sign in to the console instead.")

    @app.get("/api/keys")
    async def list_keys(request: Request) -> list[dict]:
        browser_only(request)
        return store.keys(request.state.user)

    @app.post("/api/keys")
    async def create_key(body: KeyRequest, request: Request) -> dict:
        """The new key, shown this once: only a hash of it is kept."""
        browser_only(request)
        return store.create_key(request.state.user, body.name)

    @app.delete("/api/keys/{prefix}")
    async def revoke_key(prefix: str, request: Request) -> dict:
        browser_only(request)
        if not store.revoke_key(request.state.user, prefix):
            raise HTTPException(404, "No key of yours starts with that.")
        return {"revoked": prefix}

    @app.post("/api/v1/ask")
    async def connector_ask(body: ConnectorAskRequest, request: Request) -> dict:
        """Ask a question and get the whole answer back as one JSON result."""
        try:
            result = await answer_once(request.state.user, body.question, body.fresh)
        except LimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
        if result["error"]:
            raise HTTPException(502, f"The run stopped: {result['error']}")
        return {"question": body.question, **result}

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
        for path in sorted((p for d in trace_dirs for p in d.glob("*.json")), key=lambda p: p.name, reverse=True):
            data = json.loads(path.read_text(encoding="utf-8"))
            found.append({k: data.get(k) for k in ("id", "question", "label", "created")})
        return found

    @app.get("/api/traces/{trace_id}")
    async def trace(trace_id: str) -> dict:
        for folder in trace_dirs:
            path = folder / f"{trace_id}.json"
            if path.parent == folder and path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        raise HTTPException(404, "No saved run with that id.")

    @app.post("/api/ask")
    async def ask(request: AskRequest, http: Request) -> StreamingResponse:
        try:
            events = await ask_events(http.state.user, request.question, request.interactive, request.fresh)
        except LimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
        return streamed(events)

    @app.post("/api/resume")
    async def resume(request: ResumeRequest) -> StreamingResponse:
        try:
            store.check_budget(limits)  # the rest of a paused run still spends tokens
        except LimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
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
        return streamed(run_events(snapshot.values["question"], request.thread_id, stream, earlier,
                                   store.question_for_thread(request.thread_id)))

    if WEB_DIST.exists():  # production: the built console is served by the same process
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()
