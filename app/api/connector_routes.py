"""Routes for connections to outside tools: connect, import into a data source, send results out, export.

All of them act for the signed-in user; API keys may use them too, except signing in with Google, which
needs a person in a browser.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.connectors import kinds
from app.connectors.http import ConnectorError
from app.connectors.store import ConnectionStore
from app.connectors.tables import to_csv, to_excel
from app.sources.runtime import SourceRuntime
from app.sources.store import SourceStore


class ConnectRequest(BaseModel):
    kind: str = Field(pattern=r"^[a-z]{2,20}$")
    name: str = Field(default="", max_length=80)
    values: dict[str, str] = Field(default_factory=dict, description="The kind's fields, secret ones included")


class ImportRequest(BaseModel):
    item: str | None = Field(default=None, max_length=200, description="Table, sheet, file or tool to import")
    name: str = Field(default="", max_length=80, description="Name of the new data source")
    url: str | None = Field(default=None, max_length=2000, description="For a file at a link")
    arguments: dict[str, Any] = Field(default_factory=dict, description="For an MCP tool")


class SendRequest(BaseModel):
    trace_id: str | None = Field(default=None, max_length=200, description="The answer to send")
    tool: str | None = Field(default=None, max_length=200, description="For an MCP server: the tool that receives it")
    argument: str | None = Field(default=None, max_length=100, description="For an MCP server: the tool's text argument")


class TablesRequest(BaseModel):
    name: str = Field(default="Analyst results", max_length=100)
    tables: list[dict] = Field(default_factory=list, max_length=50)


def payload_from_trace(trace: dict) -> dict:
    """The question, the answer and the computed tables of a saved run, ready to send anywhere."""
    events = trace.get("events", [])
    done = next((e for e in reversed(events) if e.get("type") == "done"), {})
    facts = next((e["data"].get("facts", {}) for e in reversed(events)
                  if e.get("node") in ("analyze", "analyze_partial")), {})
    tables = [{"name": s.get("purpose") or s["id"], "rows": s.get("rows", [])} for s in facts.get("steps", [])]
    return {"question": trace.get("question", ""), "answer": done.get("answer", ""), "tables": tables}


def add_connector_routes(app: FastAPI, connections: ConnectionStore, sources: SourceStore, runtime: SourceRuntime,
                         load_trace: Callable[[str], dict]) -> None:
    def kind_of(name: str):
        kind = kinds.KINDS.get(name)
        if not kind:
            raise HTTPException(404, f"There is no '{name}' connector.")
        return kind

    def owned(request: Request, connection_id: str) -> dict:
        try:
            return connections.get(request.state.user, connection_id)
        except KeyError as exc:
            raise HTTPException(404, "No connection of yours has that id.") from exc

    def runner(request: Request, connection: dict):
        """The kind's implementation; Google keeps renewed access tokens, encrypted, as it goes."""
        kind = kinds.KINDS[connection["kind"]]
        if kind is kinds.Google:
            return kind(on_refresh=lambda secret: connections.update_secret(request.state.user, connection["id"], secret))
        return kind()

    async def guarded(work):
        try:
            return await work
        except ConnectorError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, f"Something is missing or wrong: {exc}") from exc

    def as_source(owner: str, name: str, tables: list[dict]) -> dict:
        """Imported tables become a data source like an upload: one CSV file per table, then the usual build."""
        files = [(f"{t['name'][:80]}.csv", to_csv(t["rows"])) for t in tables if t["rows"]]
        if not files:
            raise HTTPException(422, "The import found no rows.")
        try:
            source = sources.add_csv(owner, name or tables[0]["name"], files)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        runtime.start_build(source["id"])
        return {**source, "tables_found": [{"name": t["name"], "rows": len(t["rows"])} for t in tables]}

    @app.get("/api/connectors/catalog")
    async def catalog() -> dict:
        return {"kinds": kinds.describe()}

    @app.get("/api/connectors")
    async def list_connections(request: Request) -> list[dict]:
        return connections.list(request.state.user)

    @app.post("/api/connectors")
    async def connect(body: ConnectRequest, request: Request) -> dict:
        """Check the fields, try the service once, then keep the credential encrypted (never shown again)."""
        kind = kind_of(body.kind)
        if getattr(kind, "oauth", False):
            raise HTTPException(422, f"{kind.label} connects by signing in, not with a form.")
        settings, secret = {}, {}
        for spec in kind.fields:
            value = (body.values.get(spec["key"]) or "").strip()
            if spec["required"] and not value:
                raise HTTPException(422, f"Fill in '{spec['label']}'.")
            if value:
                (secret if spec["secret"] else settings)[spec["key"]] = value
        implementation = kind()
        if hasattr(implementation, "check"):
            await guarded(_sync(implementation.check, secret))
        if hasattr(implementation, "items") and kind is not kinds.HubSpot:  # one real call proves the credential works
            await guarded(implementation.items(settings, secret))
        return connections.add(request.state.user, body.kind, body.name or kind.label, settings, secret or None)

    @app.delete("/api/connectors/{connection_id}")
    async def disconnect(connection_id: str, request: Request) -> dict:
        owned(request, connection_id)
        connections.remove(request.state.user, connection_id)
        return {"removed": connection_id}

    # Declared before the routes with an id, or "link" would be taken for one.
    @app.post("/api/connectors/link/import")
    async def import_link(body: ImportRequest, request: Request) -> dict:
        """A file at a public link needs no saved connection."""
        if not body.url:
            raise HTTPException(422, "Paste the address of the file or shared sheet.")
        tables = await guarded(kinds.Link().fetch({}, {}, None, {"url": body.url, "name": body.name}))
        return as_source(request.state.user, body.name, tables)

    @app.get("/api/connectors/{connection_id}/items")
    async def items(connection_id: str, request: Request) -> list[dict]:
        connection = owned(request, connection_id)
        implementation = runner(request, connection)
        if not hasattr(implementation, "items"):
            return []
        secret = connections.secret(request.state.user, connection_id)
        return await guarded(implementation.items(connection["settings"], secret))

    @app.post("/api/connectors/{connection_id}/import")
    async def import_from(connection_id: str, body: ImportRequest, request: Request) -> dict:
        connection = owned(request, connection_id)
        implementation = runner(request, connection)
        if not connection or not kinds.KINDS[connection["kind"]].imports:
            raise HTTPException(422, "This connection cannot bring data in.")
        secret = connections.secret(request.state.user, connection_id)
        options = {"name": body.name, "url": body.url, "arguments": body.arguments}
        tables = await guarded(implementation.fetch(connection["settings"], secret, body.item, options))
        return as_source(request.state.user, body.name, tables)

    @app.post("/api/connectors/{connection_id}/send")
    async def send(connection_id: str, body: SendRequest, request: Request) -> dict:
        connection = owned(request, connection_id)
        if not kinds.KINDS[connection["kind"]].exports:
            raise HTTPException(422, "This connection cannot receive results.")
        if not body.trace_id:
            raise HTTPException(422, "Choose the answer to send.")
        payload = {**payload_from_trace(load_trace(body.trace_id)), "tool": body.tool, "argument": body.argument}
        secret = connections.secret(request.state.user, connection_id)
        return {"message": await guarded(runner(request, connection).send(connection["settings"], secret, payload))}

    @app.post("/api/export/xlsx")
    async def export_excel(body: TablesRequest) -> StreamingResponse:
        """Every table of an answer in one Excel workbook, one sheet each."""
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in body.name).strip() or "results"
        return StreamingResponse(io.BytesIO(to_excel(body.tables)), headers={
            "Content-Disposition": f'attachment; filename="{safe[:60]}.xlsx"'},
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ---- sign in with Google -------------------------------------------------------------------

    @app.get("/api/connectors/google/start")
    async def google_start(request: Request, name: str = "Google Sheets & Drive") -> dict:
        if request.state.via == "key":
            raise HTTPException(403, "Signing in with Google needs a person in a browser.")
        if not kinds.google_configured():
            raise HTTPException(409, "Google sign-in is not set up on this console yet (GOOGLE_CLIENT_ID and "
                                     "GOOGLE_CLIENT_SECRET). See docs/connectors.md.")
        state, verifier = connections.new_state(request.state.user, "google", name[:80])
        redirect = kinds.google_redirect(str(request.base_url))
        return {"url": kinds.google_authorize_url(state, verifier, redirect)}

    @app.get("/api/connectors/google/callback")
    async def google_callback(request: Request, state: str = "", code: str = "", error: str = "") -> RedirectResponse:
        waiting = connections.take_state(state) if state else None
        if error or not waiting or waiting["owner"] != request.state.user:  # forged, expired or someone else's
            return RedirectResponse("/#connections?google=refused", status_code=303)
        try:
            secret = await kinds.google_exchange(code, waiting["verifier"], kinds.google_redirect(str(request.base_url)))
        except ConnectorError:
            return RedirectResponse("/#connections?google=failed", status_code=303)
        connections.add(request.state.user, "google", waiting["name"], {}, secret)
        return RedirectResponse("/#connections?google=connected", status_code=303)


async def _sync(check, secret):
    check(secret)


__all__ = ["add_connector_routes", "json", "payload_from_trace"]
