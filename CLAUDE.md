# analyst-agents

Multi-agent data analyst that answers multi-step business questions over a governed semantic layer (dbt + MetricFlow) exposed as MCP tools, measured with a repeated-run evaluation harness. Portfolio project for AI-engineer roles. Full history, every fix and 39 interview stories: `docs/DEV_LOG.md`.

## Environment

- Windows laptop, Python 3.12, virtual env `.agent`. Activate in PowerShell: `.agent\Scripts\Activate.ps1`; in Git Bash: `source .agent/Scripts/activate`.
- LLM: Groq free tier only, model `openai/gpt-oss-120b` (`GROQ_API_KEY` in `.env`, never committed; `GROQ_MODEL` overrides). Limits: 8K tokens/minute, 200K tokens/day. Gemini is not available from the user's location.
- Server: Hetzner CX22 (Ubuntu 24.04), reachable only over Tailscale; its address is in the `DEPLOY_HOST` GitHub secret. Deployed by CI (`scripts/deploy.sh` via a forced-command SSH key), never by hand. Two containers from one image: `analyst-agents-mcp` (`127.0.0.1:8100`) and `analyst-agents-console` (`127.0.0.1:8200`). The console is public at `analyst.medamine.work` through the server's Caddy container, which reaches it on the external Docker network `web` (declared in `docker-compose.yml`; the Caddyfile itself lives outside this repo). Sign-in is required (accounts are created on the server with `docker exec -it analyst-agents-console python -m app.api.users add <name>`; there is no sign-up page). The console's Groq key comes from `~/apps/analyst-agents/.env` on the server (git-ignored, survives deploys, never in the image); after changing it, run `docker compose up -d` there.
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
8. **Console** (Phase 7a): FastAPI `app/api/` (metrics, evals, saved traces, `POST /api/ask` streaming each step as server-sent events and saving a replayable trace; with `interactive: true` it can end with an `approval` event, and `POST /api/resume {thread_id, choice}` continues the run) + React 19 / Vite 8 / Tailwind 4 in `web/` (Node 20.19+). Demo traces `traces/demo-*.json` use real data with scripted model steps and are labeled as demos. Deployed as the `console` service in `docker-compose.yml` (serves the built web app and the API on one port; starts its own MCP server over stdio). Security (`app/api/security.py`, SQLite at `checkpoints/console.sqlite`, `CONSOLE_DB` overrides): every `/api/*` route except health and sign-in needs a session cookie (HttpOnly, SameSite=Lax, 12 hours; passwords as scrypt hashes, sessions stored as hashes; 5 wrong passwords lock a name for 15 minutes); live questions stop at a shared daily token budget (`DAILY_TOKEN_BUDGET`, default 150,000) and a per-user hourly limit (`QUESTIONS_PER_HOUR`, default 30); at most `MAX_LIVE_RUNS` (2) run at once, the rest wait; every question is logged with its user, status, tokens and trace (`GET /api/history`). `CONSOLE_AUTH=off` turns sign-in off for local development. Saved answers (`app/api/answers.py`, same database): a finished run whose answer passed the number check, with no definition picked, is stored with all its events; the same question (ignoring case, spacing and final punctuation, same interactive mode) replays it with no model call, logged as `reused` and outside the budget and limits. It is reused for `SAVED_ANSWER_DAYS` (7) and only while the data range and metric definitions are unchanged. `fresh: true` (the console's "Ask again") skips it. `GET /api/suggestions?q=` returns past questions containing every typed word (most asked first, from all users) and metrics named by any typed word of 4+ letters.

Operational database (Phase 4): LangGraph checkpoints in SQLite, `checkpoints/analyst.sqlite` (`CHECKPOINT_DB` overrides; not committed). Used by the console API and the interactive command line; evals run without it. The steps already streamed for a paused console run are kept in `traces/paused/`. Postgres later.

## Commands

```powershell
# Warehouse (run from warehouse\)
dbt build
mf validate-configs
mf query --metrics revenue_pre_tax --group-by store__store_name --explain

# Repo root
ruff check .
pytest -q                                    # 85 tests (unit + integration)
pytest -q -m "not integration"
python -m app.agents.orchestrated.graph "<question>"
python -m app.agents.orchestrated.graph --interactive "<question>"   # asks which definition is meant
python -m app.agents.orchestrated.graph --resume <thread id>         # answer a paused run later
python -m app.agents.baseline "<question>"
python -m evals.run_eval --agent orchestrated --set v2 --repeats 3
python -m evals.run_eval --resume evals\results\<file>.jsonl
python -m evals.rescore evals\results\<file>.jsonl
python -m scripts.record_demo_traces             # or: ... 5  (only the fifth demo)

# Console: two terminals (sign-in is on: create a local account first, or set $env:CONSOLE_AUTH = "off")
python -m app.api.users add <name>
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

## Where we are (2026-10-14)

Live at `analyst.medamine.work` (commit `e1a0664`): Phase 4 human approval, the m06 fix, the development cache, the scripted console, the console as a compose service behind Caddy.

Plan agreed on 2026-10-14 (the user asked for saved frequent questions, better suggestions, custom databases or API sources, more security and reliability, a professional analyst interface, and connectors). Order: security, then saved answers and suggestions, then the interface, then connectors; custom databases continue as the user's hands-on exercises. For these new features Claude builds and the user reviews before anything is pushed.

1. **Security: done and deployed** (commit `51aece1`); the user created their account on the server and confirmed sign-in works.
2. **Saved answers and suggestions (built, not pushed; for the user's review).** Checked: 85 Python and 6 web tests, and in the browser with the real model (first ask 2,779 tokens; the same question reworded in lower case replayed with 0, logged as `reused`; suggestions show it as asked twice with a saved answer).
3. **Professional analyst interface:** denser layout, tables and charts, CSV export, question history, metric browser.
4. **Connectors:** the MCP server behind authentication for MCP clients, a REST API with API keys, optionally a Slack bot. Data from SaaS APIs has to be loaded into the warehouse first (every number must come from governed SQL metrics).
5. **Custom databases (hands-on):** the user redoes step 1 by hand (exercise 1: `domains/<name>/domain.yml` and `app/domain.py`); Claude's worked version is on the local branch `step1-domain-pack-reference` (not pushed). Then a second sample company, then a non-DuckDB warehouse.
6. **Re-measure v2 in full** on the current code (most of a day's quota).
7. **Put the dev log back**, then Phases 5 to 7 as before.

Known gaps: paused runs that are never answered stay in `checkpoints/` and `traces/paused/` (no expiry yet); a replan that reuses an earlier choice has no test; the reviewer checks that numbers exist in the facts, not that they are the right numbers for the sentence (the m06 miss passed review), so `top_period` narrows that hole without closing it; the virtual env runs Python 3.11, not the 3.12 named above.

Logged improvements: a glossary entry so "sales tax" maps to `tax_collected` (allowed now that the held-out run is done); replace the Tailscale allow-all rule with grants (`tag:ci` → server port 22 only); hybrid search for `search_metrics`; a `list_dimension_values` tool; claim checking in words (the reviewer only checks numbers).
