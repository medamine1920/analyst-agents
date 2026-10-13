# analyst-agents

Multi-agent data analyst that answers multi-step business questions over a governed semantic layer (dbt + MetricFlow) exposed as MCP tools, measured with a repeated-run evaluation harness. Portfolio project for AI-engineer roles. Full history, every fix and 39 interview stories: `docs/DEV_LOG.md`.

## Environment

- Windows laptop, Python 3.12, virtual env `.agent`. Activate in PowerShell: `.agent\Scripts\Activate.ps1`; in Git Bash: `source .agent/Scripts/activate`.
- LLM: Groq free tier only, model `openai/gpt-oss-120b` (`GROQ_API_KEY` in `.env`, never committed; `GROQ_MODEL` overrides). Limits: 8K tokens/minute, 200K tokens/day. Gemini is not available from the user's location.
- Server: Hetzner CX22 (Ubuntu 24.04), reachable only over Tailscale; its address is in the `DEPLOY_HOST` GitHub secret. Deployed by CI (`scripts/deploy.sh` via a forced-command SSH key), never by hand. Two containers from one image: `analyst-agents-mcp` (`127.0.0.1:8100`) and `analyst-agents-console` (`127.0.0.1:8200`). The console's Groq key comes from `~/apps/analyst-agents/.env` on the server (git-ignored, survives deploys, never in the image); after changing it, run `docker compose up -d` there.
- Repo: github.com/medamine1920/analyst-agents.

## Architecture (bottom to top; each layer reads only the one below)

1. **Seeds** `warehouse/seeds/`: 5 jaffle-shop CSVs (61,948 orders, 90,900 items, 935 customers, 10 products, 6 stores). Data runs 2024-09-01 to 2025-08-31; only Philadelphia and Brooklyn (from 2025-03-12) have orders.
2. **Warehouse** (dbt + DuckDB, `warehouse/analyst.duckdb`): 5 staging views; marts `fct_orders` (grain: order), `fct_order_items` (grain: item), `dim_customers`, `dim_stores`, `dim_products`; time spine. 21 data tests, incl. a revenue reconciliation that catches fan-out.
3. **Semantic layer** `warehouse/models/semantic/`: 5 semantic models, 12 metrics (simple, ratio, derived, cumulative). `revenue_pre_tax` vs `revenue_with_tax` is a deliberate ambiguity. `store_count` lives on the stores table so stores without orders are visible.
4. **Access** `app/semantic/layer.py`: the ONLY code that touches MetricFlow. Serves read-only through `warehouse/serve/profiles.yml` with an absolute `DUCKDB_PATH`.
5. **MCP server** `app/mcp_server/server.py` (MCP SDK v2 `MCPServer`): `search_metrics`, `list_dimensions`, `get_data_time_range`, `query_metrics`, `list_metrics`, plus `/health`. stdio locally; streamable HTTP in Docker (container 8000 → host `127.0.0.1:8100`).
6. **Agents** `app/agents/`:
   - `baseline.py`: LangGraph ReAct loop (the comparison point). `reliability.py`: shared harness (retry malformed tool calls, 8-round budget, then a forced answer). `mcp_tools.py`: own MCP → LangChain adapter.
   - `orchestrated/`: `resolve` (glossary) → `plan` (LLM, JSON-schema `Plan`, validated) → `clarify` (code; only in interactive runs with an active ambiguity: pauses with `interrupt()` and asks the user which definition is meant) → `execute` (code, MCP) → `analyze` (code) → `write` (LLM) → `review` (code). Replan up to 3 attempts, rewrite once. Files: `glossary.py` (+ `app/semantic/glossary.yml`), `plan.py`, `gateway.py`, `analysis.py`, `review.py`, `graph.py`, `cache.py` (development-only disk cache of model answers).
7. **Evals** `evals/`: question sets v1 (10), v2 (12), v3 held-out (8); `scoring.py`, `run_eval.py`, `agents.py`, `report.py`, `rescore.py`; results in `evals/results/*.jsonl`.
8. **Console** (Phase 7a): FastAPI `app/api/` (metrics, evals, saved traces, `POST /api/ask` streaming each step as server-sent events and saving a replayable trace; with `interactive: true` it can end with an `approval` event, and `POST /api/resume {thread_id, choice}` continues the run) + React 19 / Vite 8 / Tailwind 4 in `web/` (Node 20.19+). Demo traces `traces/demo-*.json` use real data with scripted model steps and are labeled as demos. Deployed as the `console` service in `docker-compose.yml` (serves the built web app and the API on one port; starts its own MCP server over stdio).

Operational database (Phase 4): LangGraph checkpoints in SQLite, `checkpoints/analyst.sqlite` (`CHECKPOINT_DB` overrides; not committed). Used by the console API and the interactive command line; evals run without it. The steps already streamed for a paused console run are kept in `traces/paused/`. Postgres later.

## Commands

```powershell
# Warehouse (run from warehouse\)
dbt build
mf validate-configs
mf query --metrics revenue_pre_tax --group-by store__store_name --explain

# Repo root
ruff check .
pytest -q                                    # 72 tests (unit + integration)
pytest -q -m "not integration"
python -m app.agents.orchestrated.graph "<question>"
python -m app.agents.orchestrated.graph --interactive "<question>"   # asks which definition is meant
python -m app.agents.orchestrated.graph --resume <thread id>         # answer a paused run later
python -m app.agents.baseline "<question>"
python -m evals.run_eval --agent orchestrated --set v2 --repeats 3
python -m evals.run_eval --resume evals\results\<file>.jsonl
python -m evals.rescore evals\results\<file>.jsonl
python -m scripts.record_demo_traces             # or: ... 5  (only the fifth demo)

# Console: two terminals
uvicorn app.api.main:app --port 8001 --reload
uvicorn scripts.demo_console:app --port 8001   # instead: scripted model, no key, no tokens (example questions only)
$env:LLM_CACHE = "1"                           # reuse model answers from .llm_cache\ (command line and console, never evals)
cd web; npm install; npm run dev             # http://localhost:5173 ; also: npm test, npm run build
```

In Git Bash, use forward slashes in paths (`evals/results/<file>.jsonl`).

## Rules and conventions

- **The model plans and writes; code validates, executes, computes and reviews.** The writer may not do arithmetic, so every number it needs (totals, shares, changes, gaps, contributions) must already be in the facts built by `analysis.py`. The reviewer rejects numbers that are not in the facts.
- Never add `langchain-mcp-adapters`: it pins `mcp<2` and silently downgrades the SDK. Keep `mcp[cli]>=2,<3`.
- Raise expected tool mistakes as `ToolError` (via `anticipated()`); otherwise MCP v2 hides the message from the agent.
- Run dbt and `mf` from `warehouse/`. The app never writes to the database; serving is read-only.
- Fixing a bug: first reproduce the exact failure as a test (scripted planner/writer, in-process MCP server), then fix, then run `ruff check .` and `pytest -q`.
- Human approval: `clarify` runs again from its first line on resume, so it must never call the model or have side effects; the plan it needs is saved by `plan`. The user is asked once per term (a replan reuses the choice). Non-interactive is the default everywhere except the console's live mode, so eval results stay comparable.
- Tests never call Groq. Agents take injectable `plan_fn` / `write_fn`; database-backed tests are marked `integration`.
- **Eval integrity:** golden answers are verified with raw SQL, independently of the semantic layer. Freeze the system during a held-out run; never tune on held-out questions before running them; report the frozen numbers. Rate-limited runs count as pending, never as failures. Never resume a results file after changing code: start a fresh run.
- Saving quota: develop against the scripted console or with `LLM_CACHE=1`; check one question with `--ids m06 --repeats 3` before spending a full run. Eval results now record each run's plan, so a failure can be reproduced as a test without re-running the model.
- Token budget: orchestrated ≈ 3,400–5,300 tokens per question, baseline ≈ 8,000. A full v2 run (36) of the orchestrated agent fits in one day; baseline runs need two.
- Give the user Windows/PowerShell commands. `.sh` files keep LF endings (`.gitattributes`).
- Never commit `.env`, `.agent/`, `analyst.duckdb`, `web/node_modules`, `web/dist`, or non-demo traces. Never put the server address, keys or tokens in committed files.
- New interview stories go at the end of `docs/DEV_LOG.md` (section 17): one paragraph each, ending with "*Shows: …*". Log each phase's results and fixes there too.
- Docs and UI copy: plain language, sentence case, no unexplained jargon.
- Keep the "Where we are" section below current: update it whenever a step finishes.

## Results so far

| Set | Agent | pass@1 | All-runs pass | Tokens/question | Latency |
|---|---|---|---|---|---|
| v2 (12 q × 3) | baseline (with harness) | 72% | 67% | 8,260 | 42.9 s |
| v2 | orchestrated (before the Phase 3C fixes) | 89% | 83% | 4,363 | 28.0 s |
| v2 | orchestrated (after the fixes, 2026-10-09) | 100% | 100% | 4,670 | 28.4 s |
| v3 held-out (8 q × 3, frozen) | baseline | 83% | 50% | 7,893 | 37.4 s |
| v3 held-out (frozen) | orchestrated | 88% | 88% | 3,400 | 18.8 s |

Headline: on held-out questions, reliability 50% → 88% with 57% fewer tokens and 50% lower latency. The orchestrated agent's misses were systematic (fixable); the baseline's were random.

The 100% on v2 is a regression check, not a headline, and it is not the whole truth: v2 is the set the fixes were developed on, and a spot check right after that run (`..._20261009-1604.jsonl`) failed m06 once. The planner asked for new customers by month and, separately, by store over all dates, and the answer gave Brooklyn's all-time 493 as March's 255. Counting both files, m06 is 3/4 on that version. Fixed on 2026-10-13 (see below) and not yet re-measured on the full set.

## Where we are (2026-10-13)

Done since the last commit (`777bf42`), all uncommitted: v2 re-measure (100% / 100%, then the m06 miss above); Phase 4 human approval (graph, command line, API, console); the m06 fix (`ensure_drilldown` adds the period × category query when a plan splits them, `top_period` in the facts gives the top period with its own rows, every step states the dates it covers); eval results record the plan; a development cache for model answers (`LLM_CACHE=1`); a scripted console (`scripts/demo_console.py`). m06 passed 3/3 live after the fix, but none of those three plans had the split shape, so the fix itself is proven only by the scripted test.

1. **Confirm the console deploy** (added 2026-10-13): the Docker build with the web stage was not testable locally (no Docker daemon on the laptop), so check the Actions run and `docker ps` on the server after the first push.
2. **Re-measure v2 in full** on the current code: `python -m evals.run_eval --agent orchestrated --set v2 --repeats 3`. It takes most of a day's quota (about 5,000 tokens per run now), so do it on a day with no other live testing.
3. **Put the dev log back.** `docs/DEV_LOG.md` here holds only the entries from 2026-10-09 onwards; merge them into the original file (stories 40 and 41 included).
4. **Review the console by eye** (approval card, "You" step, spacing): start the scripted console and `npm run dev`. It has only been checked as text and behavior.
5. **Phase 5, memory:** remember resolved definitions per team so the same ambiguity is not asked twice. The `choices` field in the graph state is the place to pre-fill.
6. **Phase 6, traces and cost:** Arize Phoenix (OpenTelemetry) per step; cost per question.
7. **Phase 7, polish:** README (architecture diagram, results table, demo GIF); put the deployed console behind Tailscale or auth (today it is bound to the server's localhost only); a fresh held-out set (v4) to confirm the fixes generalize; final interview-story compilation; resume line update.

Known gaps: paused runs that are never answered stay in `checkpoints/` and `traces/paused/` (no expiry yet); a replan that reuses an earlier choice has no test; the reviewer checks that numbers exist in the facts, not that they are the right numbers for the sentence (the m06 miss passed review), so `top_period` narrows that hole without closing it; the virtual env runs Python 3.11, not the 3.12 named above.

Logged improvements: a glossary entry so "sales tax" maps to `tax_collected` (allowed now that the held-out run is done); replace the Tailscale allow-all rule with grants (`tag:ci` → server port 22 only); hybrid search for `search_metrics`; a `list_dimension_values` tool; claim checking in words (the reviewer only checks numbers).
