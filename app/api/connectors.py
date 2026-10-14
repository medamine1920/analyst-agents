"""Connectors: how other applications use the analyst.

Two doors, both authenticated with an API key created on the console's Connectors page
(`Authorization: Bearer ak_...`), both acting as the key's owner (same budget, limits, audit log):

- REST: POST /api/v1/ask {"question": ...} returns one JSON result: the answer, whether it passed
  the number check, every computed table, and what it cost. For scripts, spreadsheets, BI tools.
- MCP: /mcp (streamable HTTP, stateless, JSON responses) with the tools ask_analyst and list_metrics,
  for MCP clients such as Claude Desktop or Cursor. Unlike the semantic-layer MCP server, which hands
  out raw metrics, this one answers with the checked analyst.

Connector runs are never interactive: an ambiguous term is answered with every definition.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from app.api.security import LimitReached

# The user an API key belongs to, for the request being handled (set by the API's middleware).
REQUEST_USER: ContextVar[str | None] = ContextVar("request_user", default=None)

MCP_INSTRUCTIONS = """\
Answers business questions from governed metrics. Call ask_analyst with a plain-language question;
it plans the queries, runs them, computes every number in code and checks the answer. Prefer it to
doing arithmetic yourself, and quote its numbers as given. list_metrics shows what can be asked about.
"""


def result_from_events(events: list[dict]) -> dict:
    """One JSON result for a finished run (live or saved), from the events the console streams."""
    done = next((e for e in reversed(events) if e.get("type") == "done"), None)
    error = next((e for e in reversed(events) if e.get("type") == "error"), None)
    steps = [e for e in events if e.get("type") == "step"]
    reviews = [s for s in steps if s.get("node") == "review"]
    analyses = [s for s in steps if s.get("node") in ("analyze", "analyze_partial")]
    reused = next((e for e in events if e.get("type") == "reused"), None)
    saved = next((e for e in events if e.get("type") == "saved"), None)
    facts = analyses[-1]["data"].get("facts", {}) if analyses else {}
    issues = reviews[-1]["data"].get("issues", []) if reviews else None
    usage = (done or {}).get("usage") or {}
    return {
        "answer": (done or {}).get("answer", ""),
        "checked": issues == [],  # every number in the answer comes from the computed results
        "check_issues": issues or [],
        "reused": bool(reused),
        "saved_at": reused["saved_at"] if reused else None,
        "trace_id": (reused or {}).get("trace_id") or (saved or {}).get("id"),
        "data": [{"id": s["id"], "description": s.get("purpose") or s["id"], "period": s.get("period"),
                  "rows": s.get("rows", []), **({"totals": s["totals"]} if s.get("totals") else {})}
                 for s in facts.get("steps", [])],
        "definitions": facts.get("definitions", {}),
        "tokens": 0 if reused else usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        "seconds": (done or {}).get("elapsed_s"),
        "error": error["message"] if error else None,
    }


def build_mcp_app(ask: Callable[[str, str, bool, str], Awaitable[dict]],
                  metrics: Callable[[str, str], Awaitable[list[dict]]]):
    """The analyst as an MCP server, served over HTTP from inside the console."""
    server = MCPServer(name="analyst-console", instructions=MCP_INSTRUCTIONS)

    @server.tool(description="Answer a business question from the governed metrics, with every number "
                             "computed and checked in code. Returns the answer, the data behind it, and its cost. "
                             "A repeated question returns the saved answer at no cost unless fresh is true.")
    async def ask_analyst(question: str, fresh: bool = False, source: str = "sample") -> dict:
        user = REQUEST_USER.get()
        if not user:
            raise ToolError("Not signed in: send an API key as 'Authorization: Bearer ak_...'.")
        if not 3 <= len(question.strip()) <= 500:
            raise ToolError("Ask a question of 3 to 500 characters.")
        try:
            result = await ask(user, question, fresh, source)
        except (LimitReached, LookupError) as exc:
            raise ToolError(str(exc)) from exc
        if result["error"]:
            raise ToolError(f"The run stopped: {result['error']}")
        return result

    @server.tool(description="The metrics the analyst can use: name, label, type, definition, and the "
                             "dimensions each can be broken down by. source is 'sample' (the demo shop) or the "
                             "id of one of your own data sources.")
    async def list_metrics(source: str = "sample") -> list[dict]:
        user = REQUEST_USER.get()
        try:
            return await metrics(user, source)
        except LookupError as exc:
            raise ToolError(str(exc)) from exc

    # Stateless, JSON responses: every call stands alone, so restarts and several workers are fine.
    # Host checks guard servers without authentication; this endpoint requires an API key instead.
    return server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
