"""Call every MCP tool like an agent would.

Local, over stdio (launches the server as a subprocess):
    python scripts/mcp_smoke.py

Against a running HTTP server (for example through an SSH tunnel):
    python scripts/mcp_smoke.py http://localhost:8000/mcp
"""

import asyncio
import json
import sys

from mcp import Client, StdioServerParameters

SERVER = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server.server"])

CALLS = [
    ("search_metrics", {"query": "revenue"}),
    ("list_dimensions", {"metrics": ["revenue_pre_tax"]}),
    ("get_data_time_range", {}),
    (
        "query_metrics",
        {
            "metrics": ["revenue_pre_tax", "order_count"],
            "group_by": ["store__store_name"],
            "order_by": ["-revenue_pre_tax"],
        },
    ),
]


async def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else SERVER
    async with Client(target) as client:
        tools = await client.list_tools()
        print("Tools:", ", ".join(tool.name for tool in tools.tools))
        for name, arguments in CALLS:
            result = await client.call_tool(name, arguments)
            data = result.structured_content
            if isinstance(data, dict) and "sql" in data:
                data = {key: value for key, value in data.items() if key != "sql"}
            print(f"\n== {name} {arguments}")
            print(json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
