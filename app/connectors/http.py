"""The one way connectors reach the outside world: public addresses only, bounded, and retried.

- Every hop is checked: the first address and each redirect must resolve to a public address, so a
  link or a webhook cannot be pointed at the server's own network.
- Every call has a timeout and a size limit, and transient failures (timeouts, 429, 5xx) are retried
  with growing waits. Anything else fails at once with a message a person can act on.

Tests swap TRANSPORT for an httpx.MockTransport, so no test reaches the internet.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlparse

import httpx

from app.sources.store import check_host

TIMEOUT_S = 30
MAX_BYTES = 50_000_000
RETRIES = 3
MAX_REDIRECTS = 5
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests replace this


class ConnectorError(Exception):
    """A failure to show the user as is: what went wrong and, where possible, what to do."""


def check_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ConnectorError("Use a full address starting with https://.")
    try:
        check_host(parsed.hostname)
    except ValueError as exc:
        raise ConnectorError(str(exc)) from exc
    return url


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=TRANSPORT, timeout=TIMEOUT_S, follow_redirects=False,
                             headers={"User-Agent": "AnalystConsole/1.0"})


async def request(method: str, url: str, *, headers: dict | None = None, json: object = None,
                  data: object = None, params: dict | None = None, max_bytes: int = MAX_BYTES) -> httpx.Response:
    """One call with safety checks, redirects followed by hand (each one checked), and retries."""
    attempt, wait = 0, 1.0
    while True:
        attempt += 1
        try:
            async with client() as http:
                target, method_now = url, method
                for _ in range(MAX_REDIRECTS + 1):
                    check_url(target)
                    response = await http.request(method_now, target, headers=headers, json=json, data=data,
                                                  params=params)
                    if response.is_redirect and response.headers.get("location"):
                        target = urljoin(target, response.headers["location"])
                        method_now, json, data = ("GET", None, None) if response.status_code in (301, 302, 303) else (method_now, json, data)
                        continue
                    break
                else:
                    raise ConnectorError("Too many redirects.")
                if len(response.content) > max_bytes:
                    raise ConnectorError(f"The response is larger than {max_bytes // 1_000_000} MB.")
        except httpx.TimeoutException as exc:
            if attempt <= RETRIES:
                await asyncio.sleep(wait)
                wait *= 2
                continue
            raise ConnectorError("The service did not answer in time. Try again later.") from exc
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Could not reach the service: {exc.__class__.__name__}.") from exc
        if (response.status_code == 429 or response.status_code >= 500) and attempt <= RETRIES:
            retry_after = response.headers.get("retry-after", "")
            await asyncio.sleep(min(float(retry_after), 30) if retry_after.isdigit() else wait)
            wait *= 2
            continue
        if response.status_code in (401, 403):
            raise ConnectorError("The service refused the credentials: check the token or its permissions.")
        if response.status_code == 404:
            raise ConnectorError("Nothing was found at that address or id.")
        if response.status_code >= 400:
            raise ConnectorError(f"The service answered with an error ({response.status_code}): {response.text[:200]}")
        return response
