"""Routes for the data sources a user adds: list, add (CSV, database file, live database), rebuild, remove.

Every route is scoped to the signed-in user: a source belongs to whoever added it, and anyone else
gets the same "no such source" answer whether it exists or not.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.sources.runtime import SourceRuntime
from app.sources.store import MAX_UPLOAD_MB, SAMPLE, SourceStore

SAMPLE_ENTRY = {"id": SAMPLE, "name": "Sample coffee shop", "kind": "sample", "status": "ready", "error": None,
                "description": "The governed demo data: orders, items, customers, stores and products."}


class ConnectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern="^(postgres|mysql)$")
    host: str = Field(min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=255)
    user: str = Field(min_length=1, max_length=255)
    password: str = Field(default="", max_length=1000)
    schema_name: str | None = Field(default=None, max_length=255, description="Only this schema's tables")
    tables: str | None = Field(default=None, max_length=2000, description="Only these tables, separated by commas")


async def read_limited(upload: UploadFile) -> bytes:
    """The file's bytes, refusing anything past the size limit before it is all in memory."""
    limit = int(MAX_UPLOAD_MB * 1e6)
    content = await upload.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, f"{upload.filename} is larger than {MAX_UPLOAD_MB:.0f} MB.")
    return content


def add_source_routes(app: FastAPI, sources: SourceStore, runtime: SourceRuntime) -> None:
    def owned(request: Request, source_id: str) -> dict:
        try:
            return sources.get(request.state.user, source_id)
        except KeyError as exc:
            raise HTTPException(404, "No data source of yours has that id.") from exc

    def started(source: dict) -> dict:
        runtime.start_build(source["id"])
        return source

    @app.get("/api/sources")
    async def list_sources(request: Request) -> list[dict]:
        """The sample shop, then the user's own sources, newest first."""
        return [SAMPLE_ENTRY, *sources.list(request.state.user)]

    @app.get("/api/sources/{source_id}")
    async def get_source(source_id: str, request: Request) -> dict:
        return SAMPLE_ENTRY if source_id == SAMPLE else owned(request, source_id)

    @app.post("/api/sources/csv")
    async def add_csv(request: Request, name: Annotated[str, Form()], files: Annotated[list[UploadFile], File()]) -> dict:
        """CSV files, one table each. The build starts at once; poll the source for its status."""
        contents = [(f.filename or "file.csv", await read_limited(f)) for f in files]
        try:
            return started(sources.add_csv(request.state.user, name, contents))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/sources/file")
    async def add_file(request: Request, name: Annotated[str, Form()], file: Annotated[UploadFile, File()]) -> dict:
        """A SQLite or DuckDB database file, queried read-only where it is."""
        content = await read_limited(file)
        try:
            return started(sources.add_file(request.state.user, name, file.filename or "", content))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/sources/connection")
    async def add_connection(body: ConnectionRequest, request: Request) -> dict:
        """A live Postgres or MySQL database, attached read-only; the password is stored encrypted."""
        try:
            return started(sources.add_connection(request.state.user, body.name, body.kind, body.host, body.port,
                                                   body.database, body.user, body.password, body.schema_name,
                                                   (body.tables or "").split(",")))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/sources/{source_id}/rebuild")
    async def rebuild(source_id: str, request: Request) -> dict:
        """Look at the tables again (new columns, new tables) and regenerate the metrics."""
        source = owned(request, source_id)
        runtime.start_build(source_id)
        return {**source, "status": "building"}

    @app.delete("/api/sources/{source_id}")
    async def remove(source_id: str, request: Request) -> dict:
        owned(request, source_id)
        await runtime.close(source_id)
        sources.remove(request.state.user, source_id)
        return {"removed": source_id}
