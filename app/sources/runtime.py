"""Running user data sources: building them in the background, and one query process per source in use.

Each source is queried by its own MCP server process (the same server as the sample shop, pointed
at the source's generated project). A process costs memory (MetricFlow loads the whole project), so
only MAX_OPEN_SOURCES stay open; the least recently used one is closed when another is needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path

from mcp import Client, StdioServerParameters

from app.agents.orchestrated.gateway import McpGateway
from app.sources.store import SourceStore

BUILD_TIMEOUT_S = 600
MAX_OPEN_SOURCES = int(os.environ.get("MAX_OPEN_SOURCES", "3"))
REPO_ROOT = Path(__file__).resolve().parents[2]


async def build_source(store: SourceStore, source_id: str) -> None:
    """Build in a separate process, so a slow or broken source cannot stall the console."""
    folder = store.folder / source_id
    store.mark(source_id, "building")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.sources.build", str(folder), cwd=REPO_ROOT,
        env={**os.environ, **store.environment(source_id)},
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), BUILD_TIMEOUT_S)
    except TimeoutError:
        process.kill()
        store.mark(source_id, "failed", f"The build took longer than {BUILD_TIMEOUT_S // 60} minutes and was stopped.")
        return
    result_file = folder / "result.json"
    if not result_file.exists():
        tail = stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        store.mark(source_id, "failed", " ".join(tail)[:600] or "The build stopped without saying why.")
        return
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if result.get("ok"):
        store.mark(source_id, "ready", None, {k: result[k] for k in ("tables", "metrics", "notes", "time_dimensions")})
    else:
        store.mark(source_id, "failed", result.get("error"))


class SourceConnection:
    """One source's query process, owned by a task of its own (MCP clients must close where they opened)."""

    def __init__(self, params: StdioServerParameters) -> None:
        self._params = params
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.gateway: McpGateway | None = None
        self.error: BaseException | None = None

    async def start(self) -> McpGateway:
        self._task = asyncio.create_task(self._run())
        await self._ready.wait()
        if self.error:
            raise self.error
        return self.gateway

    async def _run(self) -> None:
        try:
            async with Client(self._params) as client:
                self.gateway = McpGateway(client)
                self._ready.set()
                await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - reported to whoever is waiting on start()
            self.error = exc
            self._ready.set()

    async def close(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)


class SourceRuntime:
    def __init__(self, store: SourceStore) -> None:
        self.store = store
        self._open: OrderedDict[str, SourceConnection] = OrderedDict()
        self._lock = asyncio.Lock()
        self._builds: set[asyncio.Task] = set()

    def start_build(self, source_id: str) -> None:
        task = asyncio.create_task(self._rebuild(source_id))
        self._builds.add(task)  # keep a reference until it finishes
        task.add_done_callback(self._builds.discard)

    async def _rebuild(self, source_id: str) -> None:
        await self.close(source_id)  # the old query process must not hold the workspace open
        await build_source(self.store, source_id)

    async def gateway(self, source_id: str) -> McpGateway:
        async with self._lock:
            if source_id in self._open:
                self._open.move_to_end(source_id)
                return self._open[source_id].gateway
            while len(self._open) >= MAX_OPEN_SOURCES:
                _, oldest = self._open.popitem(last=False)
                await oldest.close()
            params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server.server"],
                                           env={**os.environ, **self.store.environment(source_id)}, cwd=str(REPO_ROOT))
            connection = SourceConnection(params)
            gateway = await connection.start()
            self._open[source_id] = connection
            return gateway

    async def close(self, source_id: str) -> None:
        async with self._lock:
            connection = self._open.pop(source_id, None)
        if connection:
            await connection.close()

    async def close_all(self) -> None:
        for task in list(self._builds):
            task.cancel()
        for source_id in list(self._open):
            await self.close(source_id)
