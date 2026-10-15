"""Every kind of connector: what it needs, what it can bring in, what it can send out.

Each kind declares its fields (secret ones are encrypted and never shown again), and implements any of:
  items(settings, secret)                 -> what can be imported (tables, sheets, tools...)
  fetch(settings, secret, item, options)  -> tables, which become a data source
  send(settings, secret, payload)         -> a message about where the result went
All of them call the outside world only through connectors.http (or, for MCP, its client with the
same checks), so the same limits, retries and address checks apply everywhere.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from typing import ClassVar
from urllib.parse import urlencode, urlparse

from app.connectors import http
from app.connectors.http import ConnectorError
from app.connectors.tables import from_bytes, from_excel, table, to_csv

MAX_PAGES = 200  # paging stops here: 200 pages of 100 records is 20,000 records per import


def field(key: str, label: str, *, secret: bool = False, required: bool = True, placeholder: str = "", help: str = "") -> dict:
    return {"key": key, "label": label, "secret": secret, "required": required, "placeholder": placeholder, "help": help}


def preview(payload: dict, rows: int = 15) -> str:
    """A result as plain text for chat tools: the answer, then the first rows of each table."""
    parts = [payload.get("answer", "").strip()]
    for item in payload.get("tables", [])[:3]:
        body = item["rows"][:rows]
        if not body:
            continue
        columns = list(dict.fromkeys(k for row in body for k in row))[:6]
        lines = [" | ".join(columns)] + [" | ".join(str(row.get(c, "")) for c in columns) for row in body]
        parts.append(f"{item['name']}\n" + "\n".join(lines))
    return "\n\n".join(p for p in parts if p)[:3500]


# ---- a file or shared sheet at a link ------------------------------------------------------------

GOOGLE_SHEET = re.compile(r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)")


class Link:
    kind, label = "link", "File from a link"
    description = "A CSV, Excel or JSON file at a public address, or a Google Sheet shared as 'anyone with the link'."
    fields: ClassVar[list[dict]] = []
    imports, exports = True, False
    ask_for: ClassVar[dict] = {"url": "Address of the file or shared sheet"}

    async def fetch(self, settings: dict, secret: dict, item: str | None, options: dict) -> list[dict]:
        url = (options.get("url") or "").strip()
        name = options.get("name") or "Imported file"
        sheet = GOOGLE_SHEET.match(url)
        if sheet:  # the whole workbook, every tab
            url = f"https://docs.google.com/spreadsheets/d/{sheet.group(1)}/export?format=xlsx"
        response = await http.request("GET", url)
        if sheet and "html" in response.headers.get("content-type", ""):
            raise ConnectorError("That sheet is not shared: in Google Sheets, Share > General access > Anyone with the link.")
        try:
            return from_bytes(name, response.content, response.headers.get("content-type", ""), urlparse(url).path)
        except ValueError as exc:
            raise ConnectorError(str(exc)) from exc


# ---- any MCP server --------------------------------------------------------------------------------

class Mcp:
    kind, label = "mcp", "MCP server"
    description = "Any service with an MCP endpoint: its tools are listed, and a tool that returns records can be imported."
    fields: ClassVar[list[dict]] = [
        field("url", "MCP endpoint address", placeholder="https://example.com/mcp"),
        field("token", "Access token", secret=True, required=False, help="Sent as 'Authorization: Bearer ...'. Leave empty if none."),
    ]
    imports, exports = True, True

    async def _session(self, settings: dict, secret: dict):
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        http.check_url(settings["url"])
        headers = {"Authorization": f"Bearer {secret['token']}"} if secret.get("token") else {}
        client = http.client()
        client.headers.update(headers)
        return client, Client(streamable_http_client(settings["url"], http_client=client), read_timeout_seconds=60)

    async def _with(self, settings: dict, secret: dict, work):
        client, session = await self._session(settings, secret)
        try:
            async with client, session as connected:
                return await work(connected)
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"The MCP server could not be used: {str(exc)[:200] or exc.__class__.__name__}") from exc

    async def items(self, settings: dict, secret: dict) -> list[dict]:
        async def work(session):
            tools = await session.list_tools()
            return [{"id": t.name, "label": t.title or t.name, "description": (t.description or "")[:300],
                     "arguments": (getattr(t, "input_schema", None) or {}).get("properties", {})} for t in tools.tools]
        return await self._with(settings, secret, work)

    async def fetch(self, settings: dict, secret: dict, item: str | None, options: dict) -> list[dict]:
        if not item:
            raise ConnectorError("Choose a tool to import from.")

        async def work(session):
            result = await session.call_tool(item, options.get("arguments") or {})
            if result.is_error:
                raise ConnectorError("The tool answered with an error: "
                                     + " ".join(getattr(b, "text", "") for b in result.content)[:300])
            if result.structured_content:
                payload = result.structured_content.get("result", result.structured_content)
                try:
                    return from_bytes(item, json.dumps(payload).encode(), "application/json")
                except ValueError:
                    pass
            text = "\n".join(getattr(block, "text", "") for block in result.content).strip()
            try:
                return from_bytes(item, text.encode(), "")
            except ValueError as exc:
                raise ConnectorError("The tool did not return records (a list of rows, CSV or JSON).") from exc
        return await self._with(settings, secret, work)

    async def send(self, settings: dict, secret: dict, payload: dict) -> str:
        tool, argument = payload.get("tool"), payload.get("argument") or "text"
        if not tool:
            raise ConnectorError("Choose the tool that should receive the result.")

        async def work(session):
            result = await session.call_tool(tool, {argument: preview(payload)})
            if result.is_error:
                raise ConnectorError("The tool refused the result: " + " ".join(getattr(b, "text", "") for b in result.content)[:300])
            return f"Sent to the {tool} tool."
        return await self._with(settings, secret, work)


# ---- chat tools: results out ---------------------------------------------------------------------

class Slack:
    kind, label = "slack", "Slack"
    description = "Send an answer and its table to a channel, through an incoming webhook."
    fields: ClassVar[list[dict]] = [field("webhook_url", "Incoming webhook address", secret=True, placeholder="https://hooks.slack.com/services/...",
                    help="In Slack: Apps > Incoming Webhooks > Add to a channel.")]
    imports, exports = False, True

    def check(self, secret: dict) -> None:
        if not secret.get("webhook_url", "").startswith("https://hooks.slack.com/"):
            raise ConnectorError("A Slack webhook address starts with https://hooks.slack.com/.")

    async def send(self, settings: dict, secret: dict, payload: dict) -> str:
        self.check(secret)
        text = preview(payload)
        await http.request("POST", secret["webhook_url"], json={"text": text, "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{payload.get('question', 'Analyst answer')}*"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"```{text[:2900]}```"}}]})
        return "Sent to Slack."


TEAMS_HOSTS = (".webhook.office.com", ".logic.azure.com", ".api.powerplatform.com", ".environment.api.powerplatform.com")


class Teams:
    kind, label = "teams", "Microsoft Teams"
    description = "Send an answer and its table to a channel, through a Teams workflow webhook."
    fields: ClassVar[list[dict]] = [field("webhook_url", "Workflow webhook address", secret=True,
                    help="In Teams: the channel's Workflows > 'Post to a channel when a webhook request is received'.")]
    imports, exports = False, True

    def check(self, secret: dict) -> None:
        host = urlparse(secret.get("webhook_url", "")).hostname or ""
        if not secret.get("webhook_url", "").startswith("https://") or not host.endswith(TEAMS_HOSTS):
            raise ConnectorError("Use the webhook address Teams gives you (an office.com, logic.azure.com or powerplatform.com address).")

    async def send(self, settings: dict, secret: dict, payload: dict) -> str:
        self.check(secret)
        card = {"type": "AdaptiveCard", "version": "1.4", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "body": [{"type": "TextBlock", "text": payload.get("question", "Analyst answer"), "weight": "Bolder", "wrap": True},
                         {"type": "TextBlock", "text": preview(payload), "wrap": True, "fontType": "Monospace"}]}
        await http.request("POST", secret["webhook_url"], json={"type": "message", "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]})
        return "Sent to Teams."


# ---- tools with tables: data in --------------------------------------------------------------------

class Airtable:
    kind, label = "airtable", "Airtable"
    description = "Import the tables of an Airtable base."
    fields: ClassVar[list[dict]] = [field("base_id", "Base id", placeholder="appXXXXXXXXXXXXXX", help="In the base's address: airtable.com/app..."),
              field("token", "Personal access token", secret=True, help="airtable.com/create/tokens, with data.records:read and schema.bases:read.")]
    imports, exports = True, False
    api = "https://api.airtable.com/v0"

    def _headers(self, secret: dict) -> dict:
        return {"Authorization": f"Bearer {secret['token']}"}

    async def items(self, settings: dict, secret: dict) -> list[dict]:
        response = await http.request("GET", f"{self.api}/meta/bases/{settings['base_id']}/tables", headers=self._headers(secret))
        return [{"id": t["id"], "label": t["name"]} for t in response.json().get("tables", [])]

    async def fetch(self, settings: dict, secret: dict, item: str | None, options: dict) -> list[dict]:
        rows, offset = [], None
        for _ in range(MAX_PAGES):
            params = {"pageSize": 100, **({"offset": offset} if offset else {})}
            body = (await http.request("GET", f"{self.api}/{settings['base_id']}/{item}", headers=self._headers(secret),
                                       params=params)).json()
            rows += [{"id": r["id"], **r.get("fields", {})} for r in body.get("records", [])]
            offset = body.get("offset")
            if not offset:
                break
        return [table(options.get("name") or "Airtable table", rows)]


def notion_value(prop: dict):
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in value or [])
    if kind in ("select", "status"):
        return (value or {}).get("name")
    if kind == "multi_select":
        return ", ".join(v.get("name", "") for v in value or [])
    if kind == "date":
        return (value or {}).get("start")
    if kind == "people":
        return ", ".join(v.get("name", "") for v in value or [])
    if kind == "formula":
        return (value or {}).get((value or {}).get("type"))
    if kind in ("relation", "files", "rollup"):
        return json.dumps(value)[:500]
    return value  # number, checkbox, url, email, phone_number, created_time...


class Notion:
    kind, label = "notion", "Notion"
    description = "Import a Notion database (shared with your integration)."
    fields: ClassVar[list[dict]] = [field("token", "Internal integration secret", secret=True,
                    help="notion.so/my-integrations; then share the database with the integration (••• > Connections).")]
    imports, exports = True, False
    api = "https://api.notion.com/v1"

    def _headers(self, secret: dict) -> dict:
        return {"Authorization": f"Bearer {secret['token']}", "Notion-Version": "2022-06-28"}

    async def items(self, settings: dict, secret: dict) -> list[dict]:
        body = (await http.request("POST", f"{self.api}/search", headers=self._headers(secret),
                                   json={"filter": {"property": "object", "value": "database"}, "page_size": 100})).json()
        return [{"id": d["id"], "label": "".join(t.get("plain_text", "") for t in d.get("title", [])) or d["id"]}
                for d in body.get("results", [])]

    async def fetch(self, settings: dict, secret: dict, item: str | None, options: dict) -> list[dict]:
        rows, cursor = [], None
        for _ in range(MAX_PAGES):
            body = (await http.request("POST", f"{self.api}/databases/{item}/query", headers=self._headers(secret),
                                       json={"page_size": 100, **({"start_cursor": cursor} if cursor else {})})).json()
            rows += [{name: notion_value(prop) for name, prop in page.get("properties", {}).items()}
                     for page in body.get("results", [])]
            cursor = body.get("next_cursor") if body.get("has_more") else None
            if not cursor:
                break
        return [table(options.get("name") or "Notion database", rows)]


class HubSpot:
    kind, label = "hubspot", "HubSpot"
    description = "Import contacts, companies, deals or tickets from HubSpot."
    fields: ClassVar[list[dict]] = [field("token", "Private app access token", secret=True,
                    help="HubSpot: Settings > Integrations > Private apps, with the crm.objects.*.read scopes you need.")]
    imports, exports = True, False
    api = "https://api.hubapi.com"
    objects: ClassVar[dict] = {"contacts": "Contacts", "companies": "Companies", "deals": "Deals", "tickets": "Tickets"}

    async def items(self, settings: dict, secret: dict) -> list[dict]:
        return [{"id": k, "label": v} for k, v in self.objects.items()]

    async def fetch(self, settings: dict, secret: dict, item: str | None, options: dict) -> list[dict]:
        if item not in self.objects:
            raise ConnectorError("Choose contacts, companies, deals or tickets.")
        headers = {"Authorization": f"Bearer {secret['token']}"}
        props = (await http.request("GET", f"{self.api}/crm/v3/properties/{item}", headers=headers)).json().get("results", [])
        names = [p["name"] for p in props if not p.get("hidden") and not p.get("calculated")][:60]
        rows, after = [], None
        for _ in range(MAX_PAGES):
            params = {"limit": 100, "properties": ",".join(names), **({"after": after} if after else {})}
            body = (await http.request("GET", f"{self.api}/crm/v3/objects/{item}", headers=headers, params=params)).json()
            rows += [{"id": r["id"], **r.get("properties", {})} for r in body.get("results", [])]
            after = body.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
        return [table(options.get("name") or f"HubSpot {self.objects[item].lower()}", rows)]


# ---- Google Sheets and Drive (sign in with Google) ------------------------------------------------

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.file"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def google_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def google_redirect(base_url: str) -> str:
    return f"{os.environ.get('CONSOLE_URL', base_url).rstrip('/')}/api/connectors/google/callback"


def google_authorize_url(state: str, verifier: str, redirect: str) -> str:
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return GOOGLE_AUTH + "?" + urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": redirect, "response_type": "code",
        "scope": GOOGLE_SCOPES, "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "include_granted_scopes": "true"})


async def google_exchange(code: str, verifier: str, redirect: str) -> dict:
    body = (await http.request("POST", GOOGLE_TOKEN, data={
        "code": code, "client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})).json()
    if "refresh_token" not in body:
        raise ConnectorError("Google did not grant offline access. Remove the console from your Google account's apps and connect again.")
    return {"refresh_token": body["refresh_token"], "access_token": body["access_token"],
            "expires_at": time.time() + int(body.get("expires_in", 3600)) - 60}


class Google:
    kind, label = "google", "Google Sheets & Drive"
    description = "Sign in with Google to import your Sheets and Drive files, and to save results as a new Sheet."
    fields: ClassVar[list[dict]] = []  # nothing to type: the user signs in with Google
    imports, exports = True, True
    oauth = True

    def __init__(self, on_refresh=None) -> None:
        self.on_refresh = on_refresh  # saves a renewed access token, encrypted

    async def _token(self, secret: dict) -> str:
        if secret.get("expires_at", 0) > time.time():
            return secret["access_token"]
        body = (await http.request("POST", GOOGLE_TOKEN, data={
            "client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "refresh_token": secret["refresh_token"], "grant_type": "refresh_token"})).json()
        secret.update(access_token=body["access_token"], expires_at=time.time() + int(body.get("expires_in", 3600)) - 60)
        if self.on_refresh:
            self.on_refresh(secret)
        return secret["access_token"]

    async def items(self, settings: dict, secret: dict) -> list[dict]:
        token = await self._token(secret)
        query = (f"trashed = false and (mimeType = '{SHEET_MIME}' or mimeType = 'text/csv' or "
                 "mimeType = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')")
        body = (await http.request("GET", "https://www.googleapis.com/drive/v3/files", headers={"Authorization": f"Bearer {token}"},
                                   params={"q": query, "pageSize": 100, "orderBy": "modifiedTime desc",
                                           "fields": "files(id,name,mimeType,modifiedTime)"})).json()
        return [{"id": f["id"], "label": f["name"], "description": f.get("modifiedTime", "")[:10], "mime": f["mimeType"]}
                for f in body.get("files", [])]

    async def fetch(self, settings: dict, secret: dict, item: str | None, options: dict) -> list[dict]:
        token = await self._token(secret)
        headers = {"Authorization": f"Bearer {token}"}
        meta = (await http.request("GET", f"https://www.googleapis.com/drive/v3/files/{item}", headers=headers,
                                   params={"fields": "name,mimeType"})).json()
        name = options.get("name") or meta.get("name", "Google file")
        if meta.get("mimeType") == SHEET_MIME:
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            content = (await http.request("GET", f"https://www.googleapis.com/drive/v3/files/{item}/export", headers=headers,
                                          params={"mimeType": mime})).content
            return from_excel(name, content)
        content = (await http.request("GET", f"https://www.googleapis.com/drive/v3/files/{item}", headers=headers,
                                      params={"alt": "media"})).content
        try:
            return from_bytes(name, content, meta.get("mimeType", ""), meta.get("name", ""))
        except ValueError as exc:
            raise ConnectorError(str(exc)) from exc

    async def send(self, settings: dict, secret: dict, payload: dict) -> str:
        """A new spreadsheet in the user's Drive, one tab per table."""
        token = await self._token(secret)
        headers = {"Authorization": f"Bearer {token}"}
        tables = payload.get("tables") or [{"name": "Answer", "rows": [{"answer": payload.get("answer", "")}]}]
        titles = [re.sub(r"[\[\]:*?/\\]", " ", t["name"])[:90] or f"Table {i + 1}" for i, t in enumerate(tables)]
        sheet = (await http.request("POST", "https://sheets.googleapis.com/v4/spreadsheets", headers=headers, json={
            "properties": {"title": (payload.get("question") or "Analyst answer")[:90]},
            "sheets": [{"properties": {"title": title}} for title in titles]})).json()
        data = []
        for title, item in zip(titles, tables, strict=True):
            columns = list(dict.fromkeys(k for row in item["rows"] for k in row))
            data.append({"range": f"'{title}'!A1", "values": [columns] + [[row.get(c) for c in columns] for row in item["rows"]]})
        await http.request("POST", f"https://sheets.googleapis.com/v4/spreadsheets/{sheet['spreadsheetId']}/values:batchUpdate",
                           headers=headers, json={"valueInputOption": "RAW", "data": data})  # RAW: never run as formulas
        return f"Saved as a new Google Sheet: {sheet.get('spreadsheetUrl', '')}"


KINDS = {k.kind: k for k in (Link, Mcp, Google, Slack, Teams, Airtable, Notion, HubSpot)}
def describe() -> list[dict]:
    """The catalog the console shows: what each kind is, what it needs, whether it imports and exports."""
    return [{"kind": k.kind, "label": k.label, "description": k.description, "fields": k.fields, "imports": k.imports,
             "exports": k.exports, "oauth": getattr(k, "oauth", False),
             "available": google_configured() if k.kind == "google" else True,
             "ask_for": getattr(k, "ask_for", {})} for k in KINDS.values()]


__all__ = ["KINDS", "ConnectorError", "describe", "to_csv"]
