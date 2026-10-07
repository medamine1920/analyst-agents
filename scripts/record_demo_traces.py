"""Record demo traces for the console's replay mode, without calling any model.

The data steps are real (real graph, real MCP server, real warehouse); only the planner and the
writer are scripted. Each trace is labeled as a demo so it is never mistaken for a live run.

    python -m scripts.record_demo_traces      (from the repo root)
"""

import asyncio

from mcp import Client

from app.agents.orchestrated.gateway import McpGateway
from app.agents.orchestrated.graph import build_graph
from app.agents.orchestrated.plan import Plan
from app.api.trace import save_trace, stream_trace
from app.mcp_server.server import server

LABEL = "Demo run: data steps are real, the model's plan and wording are scripted"

DEMOS = [
    {
        "question": "Pre-tax revenue jumped in March 2025 compared with February 2025. How much did it grow, "
                    "and what drove the increase?",
        "plan": {"question_type": "change_explanation", "steps": [
            {"id": "feb", "purpose": "Pre-tax revenue in February 2025", "metrics": ["revenue_pre_tax"],
             "start_date": "2025-02-01", "end_date": "2025-02-28"},
            {"id": "mar", "purpose": "Pre-tax revenue in March 2025", "metrics": ["revenue_pre_tax"],
             "start_date": "2025-03-01", "end_date": "2025-03-31"}],
            "comparisons": [{"before_step": "feb", "after_step": "mar"}]},
        "answer": "Pre-tax revenue grew by **$26,983** (+72.88%), from $37,023 in February to $64,006 in March "
                  "2025.\n\n**What drove it:** Brooklyn had no orders in February and brought in **$19,610** in "
                  "March, 72.68% of the increase. Philadelphia added $7,373 (27.32%). The growth came from more "
                  "orders (3,446 to 6,181), while the average order value slipped from $10.74 to $10.36.\n\n"
                  "*Metric:* revenue_pre_tax, the sum of order subtotals excluding sales tax.",
    },
    {
        "question": "Which store had the higher income?",
        "plan": {"question_type": "ranking", "steps": [
            {"id": "by_store", "purpose": "Revenue by store", "metrics": ["revenue_pre_tax"],
             "group_by": ["store__store_name"]}]},
        "answer": "**Philadelphia** had the higher income under both definitions of revenue:\n\n"
                  "| Store | Revenue (pre-tax) | Revenue (with tax) |\n|---|---|---|\n"
                  "| Philadelphia | $425,467 | $450,969.65 |\n| Brooklyn | $211,977 | $220,455.72 |\n\n"
                  "\"Income\" can mean revenue before tax (revenue_pre_tax) or including the tax customers "
                  "paid (revenue_with_tax), so both are shown.",
    },
    {
        "question": "How many stores do we have, and which of them had no sales?",
        "plan": {"question_type": "listing", "steps": [
            {"id": "all_stores", "purpose": "Every store", "metrics": ["store_count"],
             "group_by": ["store__store_name"]},
            {"id": "selling_stores", "purpose": "Orders per store", "metrics": ["order_count"],
             "group_by": ["store__store_name"]}]},
        "answer": "We have **6 stores**. Four of them had no sales in the data: **Chicago, Los Angeles, New "
                  "Orleans and San Francisco**. Only Brooklyn (22,017 orders) and Philadelphia (39,931 orders) "
                  "recorded orders.\n\n*Metrics:* store_count (all stores, including those without orders) and "
                  "order_count.",
    },
    {
        "question": "Which store had the higher average order value in 2025, and by how much?",
        "plan": {"question_type": "comparison", "steps": [
            {"id": "aov_2025", "purpose": "Average order value by store in 2025",
             "metrics": ["average_order_value"], "group_by": ["store__store_name"],
             "start_date": "2025-01-01", "end_date": "2025-08-31"}]},
        "answer": "**Philadelphia** had the higher average order value in 2025: **$10.61** versus $9.63 at "
                  "Brooklyn, **$0.98 higher** per order.\n\n*Metric:* average_order_value, pre-tax revenue "
                  "divided by the number of orders.",
    },
]


async def main() -> None:
    async with Client(server) as client:
        gateway = McpGateway(client)
        for demo in DEMOS:
            plan = Plan.model_validate(demo["plan"])

            async def plan_fn(messages, plan=plan):
                return plan.model_copy(deep=True), None, {}

            async def write_fn(messages, answer=demo["answer"]):
                return answer, {}

            graph = build_graph(gateway, plan_fn, write_fn)
            events = [event async for event in stream_trace(graph, demo["question"])]
            review = [e for e in events if e.get("node") == "review"]
            issues = review[-1]["data"]["issues"] if review else ["no review step"]
            trace_id = save_trace(demo["question"], events, label=LABEL, prefix="demo-")
            print(f"{trace_id}  review issues: {issues or 'none'}")


if __name__ == "__main__":
    asyncio.run(main())
