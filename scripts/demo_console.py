"""The console API with a scripted model: no key, no tokens, real data.

    uvicorn scripts.demo_console:app --port 8001      (from the repo root; then `npm run dev` in web/)

The data steps are real (graph, MCP server, warehouse, checks, pause and resume). Only the plan and
the wording are scripted, so only the questions below can be answered. Runs are saved with a label
that says so. Use it to work on the console or to show the approval flow without spending quota.
"""

from app.agents.orchestrated.plan import Plan
from app.api.main import create_app
from app.mcp_server.server import server
from scripts.record_demo_traces import DEMOS

LABEL = "Scripted run: data steps are real, the model's plan and wording are scripted"
NOT_SCRIPTED = ("This console runs without a model, so it only answers its example questions: "
                + " | ".join(demo["question"] for demo in DEMOS))
STORE_REVENUE = {
    "revenue_pre_tax": ("revenue before tax (revenue_pre_tax)", "$425,467", "$211,977"),
    "revenue_with_tax": ("revenue including the tax customers paid (revenue_with_tax)", "$450,969.65", "$220,455.72"),
}


def demo_for(messages: list) -> dict | None:
    """Match on the start of the question, so the console's shorter example wordings work too."""
    text = messages[-1].content.removeprefix("Question: ")
    return next((demo for demo in DEMOS if text[:30] == demo["question"][:30]), None)


async def plan_fn(messages: list):
    demo = demo_for(messages)
    if demo is None:
        return None, NOT_SCRIPTED, {}
    return Plan.model_validate(demo["plan"]), None, {}


async def write_fn(messages: list):
    demo = demo_for(messages)
    chosen = next((metric for metric in STORE_REVENUE if f"chose {metric}" in messages[0].content), None)
    if chosen:  # the user picked one definition, so the answer may only use that one
        meaning, philadelphia, brooklyn = STORE_REVENUE[chosen]
        return (f"**Philadelphia** had the higher figure: **{philadelphia}** versus {brooklyn} at Brooklyn.\n\n"
                f"You chose {meaning}, so these numbers use that definition."), {}
    if "choice" in demo:  # recorded with one definition, asked without choosing: report both
        demo = next(d for d in DEMOS if d["question"] == "Which store had the higher income?")
    return demo["answer"], {}


# Local development only: no sign-in, so the console opens straight away.
app = create_app(mcp_target=server, plan_fn=plan_fn, write_fn=write_fn, trace_label=LABEL, auth=False)
