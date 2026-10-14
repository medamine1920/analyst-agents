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
import logging
import os
import time
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

from app.agents import providers
from app.agents.mcp_tools import default_target
from app.agents.orchestrated.gateway import McpGateway
from app.agents.orchestrated.graph import (
    DEFAULT_MODEL,
    open_checkpointer,
    pending_approval,
    thread_config,
)
from app.agents.orchestrated.plan import ALL_DEFINITIONS
from app.api.answers import AnswerStore, data_version, suggest
from app.api.connector_routes import add_connector_routes
from app.api.connectors import REQUEST_USER, build_mcp_app, result_from_events
from app.api.model_keys import ModelKeyStore
from app.api.projects import ProjectStore
from app.api.security import (
    SESSION_COOKIE,
    SESSION_HOURS,
    LimitReached,
    Limits,
    SecurityStore,
    TwoFactorRequired,
)
from app.api.sources_routes import add_source_routes
from app.api.trace import TRACES_DIR, save_trace, stream_trace
from app.connectors.http import ConnectorError
from app.connectors.store import ConnectionStore
from app.sources.runtime import SourceRuntime
from app.sources.store import SAMPLE, SourceStore

REPO_ROOT = Path(__file__).resolve().parents[2]
PAUSED_RUN_DAYS = 7  # a question waiting this long for the user's choice is forgotten
RESULTS_DIR = REPO_ROOT / "evals" / "results"
WEB_DIST = REPO_ROOT / "web" / "dist"
# Groq's free tier allows 8K tokens a minute, so a few runs at once already hit the limit: the rest wait.
MAX_LIVE_RUNS = int(os.environ.get("MAX_LIVE_RUNS", "2"))
OPEN_PATHS = {"/api/health", "/api/auth/login", "/api/auth/me"}
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)


def client_of(request: Request) -> tuple[str, str]:
    """(address, browser) of whoever is asking; behind the reverse proxy the address is forwarded."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else ""), request.headers.get("user-agent", "")


SOURCE_ID = r"^[a-z0-9]{1,40}$"


PROJECT_ID = r"^[a-f0-9]{10}$"


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    source: str = Field(default=SAMPLE, pattern=r"^[a-z0-9]{1,40}$")


class ProjectChange(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    source: str | None = Field(default=None, pattern=r"^[a-z0-9]{1,40}$")


class TwoFactorCode(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class PasswordOnly(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    project: str | None = Field(default=None, pattern=PROJECT_ID, description="File the question under this project")
    source: str = Field(default=SAMPLE, pattern=SOURCE_ID, description="'sample' or one of your data sources")
    interactive: bool = Field(default=False, description="Pause and ask when a business term is ambiguous")
    fresh: bool = Field(default=False, description="Run the agent even if a saved answer exists")


class ConnectorAskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    source: str = Field(default=SAMPLE, pattern=SOURCE_ID, description="'sample' or one of your data sources")
    fresh: bool = Field(default=False, description="Run the agent even if a saved answer exists")


class KeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80, description="What the key is for, e.g. 'Sales sheet'")
    days: int | None = Field(default=90, ge=1, le=730, description="Days until the key stops working; null for never")


class ModelKeyRequest(BaseModel):
    provider: str = Field(pattern="^(anthropic|openai|google|groq)$")
    key: str = Field(min_length=10, max_length=400, description="The provider's API key; stored encrypted")
    model: str | None = Field(default=None, max_length=120, description="Which model to use; the provider's default if empty")


class ModelChoice(BaseModel):
    provider: str | None = Field(default=None, pattern="^(anthropic|openai|google|groq)$",
                                 description="null: use the console's own model")
    model: str | None = Field(default=None, max_length=120)


class PasswordChange(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=10, max_length=200)


class InviteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    role: str = Field(default="user", pattern="^(user|admin)$")


class RoleChange(BaseModel):
    role: str = Field(pattern="^(user|admin)$")


class SignInRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=20, description="Authenticator or recovery code, if asked")


class ResumeRequest(BaseModel):
    thread_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    choice: str | dict[str, str] = Field(description='A metric name, or "all"; or one of those per term')


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def create_app(mcp_target: Any = None, plan_fn=None, write_fn=None, results_dir: Path = RESULTS_DIR,
               traces_dir: Path = TRACES_DIR, checkpoint_db: Path | None = None, trace_label: str = "",
               auth: bool | None = None, security_db: Path | None = None, limits: Limits | None = None,
               live_traces_dir: Path | None = None, sources_dir: Path | None = None,
               model_factory=None) -> FastAPI:
    """Build the API. Tests pass an in-process MCP server and scripted planner/writer.

    auth=None reads CONSOLE_AUTH: sign-in is required unless it is "off".
    Live runs are saved in live_traces_dir (LIVE_TRACES_DIR; on the server a folder in the volume, so
    they survive deploys); traces_dir holds the demo runs that ship with the code. Both are listed.
    model_factory(provider, model, key) builds the planner and writer for a user's own model key
    (tests pass a scripted one).
    """
    load_dotenv(REPO_ROOT / ".env")
    state: dict[str, Any] = {}
    auth = (os.environ.get("CONSOLE_AUTH", "on").lower() != "off") if auth is None else auth
    store, limits = SecurityStore(security_db), limits or Limits.from_env()
    answers = AnswerStore(store.path)  # same database file: it is the volume that survives deploys
    sources = SourceStore(store.path, sources_dir)
    projects = ProjectStore(store.path)
    connections = ConnectionStore(store.path)
    model_keys = ModelKeyStore(store.path)
    model_factory = model_factory or providers.model_fns
    runtime = SourceRuntime(sources)
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
            await sweep_once()  # at start, then with the first question after six hours
            try:
                yield
            finally:
                await runtime.close_all()  # the query processes of users' sources

    app = FastAPI(title="Analyst Console API", lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """Headers that tell browsers to refuse framing, sniffing and outside scripts, on every response."""
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https":
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/api/"):
            headers.setdefault("Cache-Control", "no-store")  # answers and keys never sit in a shared cache
        return response

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

    def browser_only(request: Request) -> None:
        if request.state.via == "key":
            raise HTTPException(403, "API keys cannot manage keys or accounts: sign in to the console instead.")

    @app.post("/api/auth/login")
    async def sign_in(body: SignInRequest, request: Request, response: Response) -> dict:
        ip, agent = client_of(request)
        try:
            token = store.sign_in(body.name, body.password, body.code, ip, agent)
        except LimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
        except TwoFactorRequired as exc:
            return JSONResponse({"detail": str(exc), "two_factor": True}, status_code=401)
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
        account = store.account(user) if auth and user else None
        return {"user": user, "auth": auth, "usage": store.usage(user, limits) if user else None,
                "role": account["role"] if account else ("admin" if user == "local" else None),
                "must_change_password": bool(account and account["must_change"]),
                "two_factor": bool(account and store.two_factor_on(user)), "model": current_model(user)}

    def current_model(user: str | None) -> dict | None:
        if not user:
            return None
        active = next((k for k in model_keys.list(user) if k["active"]), None)
        if active:
            return {"provider": active["provider"], "label": active["label"], "model": active["model"], "own": True}
        return {"provider": "server", "label": "The console's model", "own": False,
                "model": os.environ.get("GROQ_MODEL", DEFAULT_MODEL)}

    # ---- the user's own model keys -------------------------------------------------------------

    @app.get("/api/models")
    async def list_model_keys(request: Request) -> dict:
        """The providers one can add a key for, the user's keys (masked) and which model answers."""
        browser_only(request)
        return {"providers": [{"id": p.id, "label": p.label, "default": p.default, "help": p.help}
                              for p in providers.PROVIDERS.values()],
                "keys": model_keys.list(request.state.user), "current": current_model(request.state.user)}

    @app.post("/api/models/keys")
    async def add_model_key(body: ModelKeyRequest, request: Request) -> dict:
        """Check the key with the provider (by listing its models), then store it encrypted and use it."""
        browser_only(request)
        key = body.key.strip()
        try:
            models = await providers.check_key(body.provider, key)
        except ConnectorError as exc:
            raise HTTPException(422, f"The key was not accepted: {exc}") from exc
        default = providers.PROVIDERS[body.provider].default
        model = (body.model or "").strip() or (default if default in models or not models else models[0])
        if models and model not in models:
            raise HTTPException(422, f"This key cannot use {model}. It can use: {', '.join(models[:12])}")
        store.record(request.state.user, f"added a {body.provider} model key", *client_of(request))
        return model_keys.save(request.state.user, body.provider, key, model, models)

    @app.put("/api/models/active")
    async def choose_model(body: ModelChoice, request: Request) -> dict:
        browser_only(request)
        try:
            model_keys.choose(request.state.user, body.provider, body.model)
        except KeyError as exc:
            raise HTTPException(404, "Add a key for that provider first.") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"current": current_model(request.state.user)}

    @app.delete("/api/models/keys/{provider}")
    async def remove_model_key(provider: str, request: Request) -> dict:
        browser_only(request)
        if not model_keys.remove(request.state.user, provider):
            raise HTTPException(404, "No key of yours for that provider.")
        store.record(request.state.user, f"removed the {provider} model key", *client_of(request))
        return {"removed": provider, "current": current_model(request.state.user)}

    # ---- security: sessions, activity, two-step sign-in --------------------------------------

    @app.get("/api/account/sessions")
    async def list_sessions(request: Request) -> list[dict]:
        browser_only(request)
        return store.sessions(request.state.user, request.cookies.get(SESSION_COOKIE))

    @app.delete("/api/account/sessions/{session_id}")
    async def end_session(session_id: str, request: Request) -> dict:
        browser_only(request)
        ended = store.end_sessions(request.state.user, session_id, keep=request.cookies.get(SESSION_COOKIE))
        if not ended:
            raise HTTPException(404, "No other session with that id (sign out of this one from the menu).")
        return {"ended": ended}

    @app.post("/api/account/sessions/end-others")
    async def end_other_sessions(request: Request) -> dict:
        browser_only(request)
        ended = store.end_sessions(request.state.user, keep=request.cookies.get(SESSION_COOKIE))
        store.record(request.state.user, "signed out other sessions", *client_of(request))
        return {"ended": ended}

    @app.get("/api/account/activity")
    async def activity(request: Request) -> list[dict]:
        browser_only(request)
        return store.events(request.state.user)

    @app.post("/api/account/2fa/start")
    async def start_two_factor(request: Request) -> dict:
        """A new authenticator secret (shown once), not active until confirmed with a code."""
        browser_only(request)
        return store.start_two_factor(request.state.user)

    @app.post("/api/account/2fa/confirm")
    async def confirm_two_factor(body: TwoFactorCode, request: Request) -> dict:
        browser_only(request)
        codes = store.finish_two_factor(request.state.user, body.code)
        if codes is None:
            raise HTTPException(422, "That code does not match. Check the time on your phone and try the next code.")
        return {"recovery_codes": codes}

    @app.post("/api/account/2fa/stop")
    async def stop_two_factor(body: PasswordOnly, request: Request) -> dict:
        browser_only(request)
        if not store.stop_two_factor(request.state.user, body.password):
            raise HTTPException(403, "The password is not right.")
        return {"two_factor": False}

    @app.post("/api/account/password")
    async def change_password(body: PasswordChange, request: Request, response: Response) -> dict:
        """Change your own password. Every other session is signed out; this one gets a new cookie."""
        browser_only(request)
        user = request.state.user
        try:
            changed = store.change_password(user, body.current, body.new)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not changed:
            raise HTTPException(403, "The current password is not right.")
        store.record(user, "changed password", *client_of(request))
        token = store.issue_session(user, *client_of(request))
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True,
                            samesite="lax", secure=https, path="/")
        return {"changed": True}

    def admin_only(request: Request) -> None:
        browser_only(request)
        account = store.account(request.state.user) if auth else {"role": "admin"}
        if not account or account["role"] != "admin":
            raise HTTPException(403, "Only an admin can manage accounts.")

    @app.get("/api/admin/users")
    async def list_accounts(request: Request) -> list[dict]:
        admin_only(request)
        return store.accounts()

    @app.post("/api/admin/users")
    async def invite(body: InviteRequest, request: Request) -> dict:
        """A new account with a one-time password, shown this once."""
        admin_only(request)
        try:
            return {"name": body.name, "role": body.role, "temporary_password": store.invite(body.name, body.role)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/admin/users/{name}/reset")
    async def reset(name: str, request: Request) -> dict:
        admin_only(request)
        try:
            return {"name": name, "temporary_password": store.reset_password(name)}
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/admin/users/{name}/role")
    async def change_role(name: str, body: RoleChange, request: Request) -> dict:
        admin_only(request)
        try:
            store.set_role(name, body.role)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"name": name, "role": body.role}

    @app.delete("/api/admin/users/{name}")
    async def remove(name: str, request: Request) -> dict:
        admin_only(request)
        if name == request.state.user:
            raise HTTPException(422, "You cannot remove your own account.")
        if not store.remove_user(name):
            raise HTTPException(404, f"No account called '{name}'.")
        return {"removed": name}

    async def gateway_for(user: str, source: str) -> McpGateway:
        """The sample shop's gateway, or the query process of one of the user's ready sources."""
        if source == SAMPLE:
            return state["gateway"]
        try:
            found = sources.get(user, source)
        except KeyError as exc:
            raise LookupError("No data source of yours has that id.") from exc
        if found["status"] != "ready":
            raise LookupError(f"'{found['name']}' is not ready to be asked yet ({found['status']}).")
        return await runtime.gateway(source)

    async def current_data_version(user: str = "", source: str = SAMPLE) -> str:
        return data_version(await (await gateway_for(user, source)).context())

    def not_found(exc: LookupError) -> HTTPException:
        return HTTPException(404 if "No data source" in str(exc) else 409, str(exc))

    @app.get("/api/suggestions")
    async def suggestions(request: Request, q: str = "", source: str = SAMPLE) -> dict:
        """Questions asked before (most asked first) and metrics matching what is being typed."""
        user = request.state.user
        try:
            context = await (await gateway_for(user, source)).context()
        except LookupError as exc:
            raise not_found(exc) from exc
        version = data_version(context)
        past = store.recent_questions(source=source, user=None if source == SAMPLE else user)
        found = suggest(q, past, context["catalog"])
        for item in found["questions"]:
            item["saved_answer"] = answers.has(item["question"], version, source)
        return found

    @app.get("/api/history")
    async def history(request: Request, project: str | None = None, limit: int = 50) -> list[dict]:
        """The signed-in user's recent questions, newest first; `project` narrows them ("none": unfiled)."""
        return store.history(request.state.user, limit=max(1, min(limit, 200)), project=project)

    def own_project(request: Request, project_id: str) -> dict:
        try:
            return projects.get(request.state.user, project_id)
        except KeyError as exc:
            raise HTTPException(404, "No project of yours has that id.") from exc

    @app.get("/api/projects")
    async def list_projects(request: Request) -> list[dict]:
        return projects.list(request.state.user)

    @app.post("/api/projects")
    async def create_project(body: ProjectRequest, request: Request) -> dict:
        try:
            return projects.create(request.state.user, body.name, body.source)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.patch("/api/projects/{project_id}")
    async def change_project(project_id: str, body: ProjectChange, request: Request) -> dict:
        own_project(request, project_id)
        try:
            return projects.update(request.state.user, project_id, body.name, body.source)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/projects/{project_id}")
    async def remove_project(project_id: str, request: Request) -> dict:
        own_project(request, project_id)
        projects.remove(request.state.user, project_id)
        return {"removed": project_id}

    def server_model():
        if "model" not in state:
            from app.agents.orchestrated import graph as orchestrated

            # the real model is only created when a live question is asked
            state["model"] = (plan_fn, write_fn) if plan_fn and write_fn else orchestrated.model_fns()
        return state["model"]

    def model_for(user: str) -> dict:
        """The model this user's questions run on: their active key's, or the console's own.

        {"id": ..., "fns": (plan_fn, write_fn), "own": bool, "label": ...}. A user's planner and writer
        are rebuilt only when they change key or model.
        """
        choice = model_keys.active(user) if user else None
        if not choice:
            return {"id": "server", "fns": server_model(), "own": False, "label": None}
        model_id = f"{user}:{choice['provider']}:{choice['model']}:{choice['version']}"
        cached = state.setdefault("user_models", {}).get(user)
        if not cached or cached[0] != model_id:
            cached = state["user_models"][user] = (
                model_id, model_factory(choice["provider"], choice["model"], choice["key"]))
        return {"id": model_id, "fns": cached[1], "own": True, "label": f"{choice['provider']}:{choice['model']}"}

    async def graph_for(user: str, source: str, model: dict | None = None):
        """A graph per source and model, rebuilt whenever the source's query process is (after a rebuild, say)."""
        from app.agents.orchestrated import graph as orchestrated

        model = model or model_for(user)
        if source == SAMPLE:
            graphs = state.setdefault("graphs", {})
            if model["id"] not in graphs:
                graphs[model["id"]] = orchestrated.build_graph(state["gateway"], *model["fns"], state["checkpointer"])
            return graphs[model["id"]]
        gateway = await gateway_for(user, source)
        key = (source, model["id"])
        cached = state.setdefault("source_graphs", {}).get(key)
        if not cached or cached[0] is not gateway:
            name = sources.get(user, source)["name"]
            built = orchestrated.build_graph(gateway, *model["fns"], state["checkpointer"], use_glossary=False,
                                             prompts=orchestrated.source_prompts(f"the data source '{name}'"))
            state["source_graphs"][key] = cached = (gateway, built)
        return cached[1]

    async def expire_paused_runs() -> int:
        """Forget runs that have waited for the user's choice longer than PAUSED_RUN_DAYS (7).

        Their checkpoint and the steps already shown are deleted, and the log says 'expired'.
        """
        cutoff = time.time() - float(os.environ.get("PAUSED_RUN_DAYS", PAUSED_RUN_DAYS)) * 86400
        expired = store.expire_waiting(cutoff)
        for thread_id in expired:
            await state["checkpointer"].adelete_thread(thread_id)
            (paused_dir / f"{thread_id}.json").unlink(missing_ok=True)
        for leftover in paused_dir.glob("*.json") if paused_dir.exists() else []:
            if leftover.stat().st_mtime < cutoff:  # e.g. from a run whose log entry is gone
                leftover.unlink(missing_ok=True)
        return len(expired)

    async def sweep_once():
        state["swept_at"] = time.time()
        try:
            await expire_paused_runs()
        except Exception:  # a failed sweep must never stop the console; the next one retries
            logging.getLogger(__name__).exception("Could not expire paused runs")

    def spent(event: dict) -> int:
        usage = event.get("usage") or {}
        return usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

    async def run_events(question: str, thread_id: str, stream, earlier: list[dict], question_id: int | None,
                         interactive: bool = True, user: str = "", source: str = SAMPLE):
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
                trace_id = save_trace(question, collected, live_dir, label=trace_label, owner=user)
                paused_file.unlink(missing_ok=True)
                await state["checkpointer"].adelete_thread(thread_id)  # a finished run needs no checkpoint
                if question_id:
                    store.finish_question(question_id, "answered", spent(last), trace_id)
                answers.save(question, interactive, await current_data_version(user, source), collected, trace_id,
                             source)
                yield {"type": "saved", "id": trace_id}
        except (asyncio.CancelledError, GeneratorExit):
            # The caller went away mid-run (a closed tab, a dropped connection): say so in the log.
            if question_id:
                store.finish_question(question_id, "stopped")
            raise
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

    async def ask_events(user: str, question: str, interactive: bool, fresh: bool, source: str = SAMPLE,
                         project: str | None = None):
        """The saved answer, or a new logged run. Raises LimitReached (or LookupError for a source that is
        missing or not ready) before anything is spent.

        Not a generator itself, so the checks happen when it is called, not when it is read.
        """
        if time.time() - state.get("swept_at", 0) > 6 * 3600:
            await sweep_once()
        model = model_for(user)
        source_graph = await graph_for(user, source, model)
        if not fresh:
            saved = answers.find(question, interactive, await current_data_version(user, source), source)
            if saved:
                store.log_reused(user, question, saved["trace_id"], source, project)
                return saved_events(saved)
        thread_id = uuid.uuid4().hex
        question_id = store.start_question(user, question, limits, thread_id, source, project,
                                           model["label"], model["own"])
        stream = stream_trace(source_graph, question, thread_id=thread_id, interactive=interactive)
        return run_events(question, thread_id, stream, [], question_id, interactive, user, source)

    def streamed(events) -> StreamingResponse:
        async def body():
            async for event in events:
                yield sse(event)

        return StreamingResponse(body(), media_type="text/event-stream")

    async def answer_once(user: str, question: str, fresh: bool = False, source: str = SAMPLE) -> dict:
        """A whole answer as one JSON result, for connectors (never interactive)."""
        events = [event async for event in await ask_events(user, question, False, fresh, source)]
        return result_from_events(events)

    async def catalog(user: str = "", source: str = SAMPLE) -> list[dict]:
        return list((await (await gateway_for(user, source)).context())["catalog"].values())

    class McpEndpoint:
        """Hands /mcp requests to the MCP app of the current start (an ASGI app, not a request handler)."""

        async def __call__(self, scope, receive, send):
            await state["mcp_http"](scope, receive, send)

    # A plain route, not a mount: clients POST to /mcp exactly, with no redirect to /mcp/.
    app.router.routes.append(Route("/mcp", endpoint=McpEndpoint(), methods=["GET", "POST", "DELETE"]))

    @app.get("/api/keys")
    async def list_keys(request: Request) -> list[dict]:
        browser_only(request)
        return store.keys(request.state.user)

    @app.post("/api/keys")
    async def create_key(body: KeyRequest, request: Request) -> dict:
        """The new key, shown this once: only a hash of it is kept."""
        browser_only(request)
        return store.create_key(request.state.user, body.name, body.days)

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
            result = await answer_once(request.state.user, body.question, body.fresh, body.source)
        except LimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
        except LookupError as exc:
            raise not_found(exc) from exc
        if result["error"]:
            raise HTTPException(502, f"The run stopped: {result['error']}")
        return {"question": body.question, **result}

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/metrics")
    async def metrics(request: Request, source: str = SAMPLE) -> dict:
        try:
            context = await (await gateway_for(request.state.user, source)).context()
        except LookupError as exc:
            raise not_found(exc) from exc
        return {"data_range": context["data_range"], "metrics": list(context["catalog"].values())}

    add_source_routes(app, sources, runtime)

    @app.get("/api/evals")
    async def evals(request: Request) -> list[dict]:
        admin_only(request)
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
    async def eval_question(file_name: str, question_id: str, request: Request) -> list[dict]:
        admin_only(request)
        from evals.report import load_records

        path = results_dir / file_name
        if path.parent != results_dir or not path.exists():
            raise HTTPException(404, "No results file with that name.")
        keep = ("repeat", "passed", "failed_checks", "answer", "error", "latency_s", "input_tokens", "output_tokens")
        return [{k: r.get(k) for k in keep} for r in sorted(load_records(path), key=lambda r: r["repeat"])
                if r["id"] == question_id]

    def may_open(user: str, data: dict) -> bool:
        """Demo runs are public; a live run belongs to whoever asked it (or was shown it as a saved answer).
        Runs saved before owners were recorded are visible to admins only."""
        if data.get("label") or not auth:
            return True
        owner = data.get("owner")
        if owner:
            return owner == user or store.has_trace(user, data.get("id", ""))
        account = store.account(user)
        return bool(account and account["role"] == "admin")

    def read_trace(user: str, trace_id: str) -> dict:
        for folder in trace_dirs:
            path = folder / f"{trace_id}.json"
            if path.parent == folder and path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if may_open(user, data):
                    return data
        raise HTTPException(404, "No saved run with that id.")  # the same answer for "not yours" and "missing"

    @app.get("/api/traces")
    async def traces(request: Request) -> list[dict]:
        found = []
        for path in sorted((p for d in trace_dirs for p in d.glob("*.json")), key=lambda p: p.name, reverse=True):
            data = json.loads(path.read_text(encoding="utf-8"))
            if may_open(request.state.user, data):
                found.append({k: data.get(k) for k in ("id", "question", "label", "created")})
        return found

    @app.get("/api/traces/{trace_id}")
    async def trace(trace_id: str, request: Request) -> dict:
        return read_trace(request.state.user, trace_id)

    add_connector_routes(app, connections, sources, runtime, lambda trace_id: read_trace(REQUEST_USER.get(), trace_id))

    @app.post("/api/ask")
    async def ask(request: AskRequest, http: Request) -> StreamingResponse:
        if request.project:
            own_project(http, request.project)
            projects.touch(request.project)
        try:
            events = await ask_events(http.state.user, request.question, request.interactive, request.fresh,
                                      request.source, request.project)
        except LimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
        except LookupError as exc:
            raise not_found(exc) from exc
        return streamed(events)

    @app.post("/api/resume")
    async def resume(request: ResumeRequest, http: Request) -> StreamingResponse:
        model = model_for(http.state.user)
        try:
            if not model["own"]:
                store.check_budget(limits)  # the rest of a paused run still spends tokens
        except LimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
        graph = await graph_for(http.state.user, SAMPLE, model)
        snapshot = await graph.aget_state(thread_config(request.thread_id))
        waiting = pending_approval(snapshot)
        if waiting is None:
            raise HTTPException(404, "No paused question with that id. It may already have been answered, "
                                     f"or it waited more than {PAUSED_RUN_DAYS} days and expired.")
        for item in waiting["questions"]:
            allowed = [option["metric"] for option in item["options"]] + [ALL_DEFINITIONS]
            value = request.choice.get(item["term"]) if isinstance(request.choice, dict) else request.choice
            if value not in allowed:
                raise HTTPException(422, f"Choose one of {allowed} for '{item['term']}'.")
        paused_file = paused_dir / f"{request.thread_id}.json"
        saved = json.loads(paused_file.read_text(encoding="utf-8")) if paused_file.exists() else {}
        earlier = saved.get("events", [])
        stream = stream_trace(graph, "", thread_id=request.thread_id, resume=request.choice,
                              elapsed_before=earlier[-1]["elapsed_s"] if earlier else 0.0)
        return streamed(run_events(snapshot.values["question"], request.thread_id, stream, earlier,
                                   store.question_for_thread(request.thread_id), user=http.state.user))

    if WEB_DIST.exists():  # production: the built console is served by the same process
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()
