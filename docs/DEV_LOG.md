# Dev log: entries from 2026-10-09 onwards

The full dev log (earlier phases and interview stories 1 to 39) was not in this checkout when these
entries were written. Merge them into the original file: the first two belong with the phase logs,
the last one at the end of section 17.

## v2 re-measured after the Phase 3C fixes

Fresh run of the orchestrated agent on v2 (12 questions × 3 repeats, `openai/gpt-oss-120b`, commit
`777bf42`), file `evals/results/v2_orchestrated_openai-gpt-oss-120b_20261009-1542.jsonl`.

| | pass@1 | All-runs pass | Tokens/question | Latency |
|---|---|---|---|---|
| Before the fixes | 89% | 83% | 4,363 | 28.0 s |
| After the fixes | 100% | 100% | 4,670 | 28.4 s |

a02, m06 and m09 recovered, with no agent errors and no rate-limited runs. Tokens per question rose
by about 7%, because the facts now carry totals, gaps and missing groups. This is a regression check
on the set the fixes were developed on, not evidence that they generalize: that needs a new held-out
set (v4).

## Phase 4: human approval

What was built:

- A code-only `clarify` step between `plan` and `execute`. In an interactive run with an active
  ambiguity it pauses with LangGraph `interrupt()` and asks which governed definition is meant
  (each option with its label and description, plus "report all of them").
- The chosen definition replaces the others in the plan in code (metrics and formulas); the model
  is not called again. Answering "all" gives exactly the earlier behavior.
- Pauses are stored with a SQLite checkpointer (`checkpoints/analyst.sqlite`), keyed by `thread_id`.
- Command line: `--interactive` asks on the terminal; `--resume <thread id>` answers a paused run later.
- API: `/api/ask` with `interactive: true` ends with an `approval` event; `POST /api/resume
  {thread_id, choice}` continues and saves one complete trace. A finished run's checkpoint is deleted.
- Console: an approval card in the answer panel, a "You" step in the trace, and a demo trace that
  replays a run with a choice.
- Non-interactive stays the default (evals, plain command line), so results stay comparable.

How it was checked: 8 new Python tests and 1 new web test with a scripted model (67 and 4 in total);
by hand in the browser against a scripted console, including stopping the server while a question
was paused, starting a new process and resuming there; and one live question on the command line
with the real model, which paused, took the choice and planned with only the chosen metric.

Fixes along the way:

- The command line crashed while printing an answer containing a narrow no-break space on a Windows
  console (cp1252). Output now replaces characters the console cannot print.
- `--resume` with an unknown thread id printed a stack trace; it now prints one line.

Not done: no expiry for paused runs that are never answered; no test for a replan that reuses an
earlier choice.

## 2026-10-13: the m06 miss after a 100% run, and cheaper testing

A one-question spot check run minutes after the 100% v2 run failed m06
(`v2_orchestrated_openai-gpt-oss-120b_20261009-1604.jsonl`). The planner had asked for new
customers by month and, in a second query, by store over all dates. The answer said March 2025 had
283 new customers (right) and that Brooklyn supplied 493 of them (wrong: 493 is Brooklyn's total
over all dates; March's figure is 255). The review step passed it, because 493 is a number in the
facts. Counting both files, m06 was 3/4 on that version, so "100%" was one good draw.

Fix, reproduced first as a test with the split plan:

- `ensure_drilldown` (plan.py): when a plan has the same metric by period and, separately, by one
  category over the same dates, code adds the period × category query.
- `top_period` (analysis.py): for a period × category breakdown, the period with the highest total
  together with that period's own rows.
- Every step in the facts now states the dates it covers in words ("all dates in the data").
- Eval results now record each run's plan. This failure had to be reconstructed from the answer.

Live check: m06 3/3 (`..._20261013-0947.jsonl`), about 5,270 tokens per run. None of the three
plans had the split shape, so the live runs show no regression; only the scripted test exercises
the fix. A full v2 re-measure is still to do.

Cheaper testing (the daily limit of 200K tokens allows about 40 questions):

- `LLM_CACHE=1` stores each successful plan and answer on disk, keyed by model and exact messages,
  and replays it. Used by the command line and the console; evals never use it. Failed attempts
  are not stored. It saves tokens, not much time: start-up of the semantic layer dominates.
- `scripts/demo_console.py`: the console API with a scripted model. Real data steps, real pause
  and resume, no key. It answers only its example questions and labels the saved runs.

Tests: 71 Python (4 new), 4 web.

## 2026-10-14: the public console gets sign-in and a budget

With a working key on a public address, anyone could spend the day's Groq quota. Added
`app/api/security.py`: accounts created by the operator only (`python -m app.api.users add`),
scrypt password hashes, session cookies (HttpOnly, SameSite=Lax) stored only as hashes, a lock
after five wrong passwords, a shared daily token budget (150,000 of Groq's 200,000, leaving room
for evals), 30 questions per user per hour, at most two live runs at once, and an audit log of
every question with its user, status, tokens and trace. A middleware guards every `/api/*` route
except health and sign-in, so a new route cannot be left open by accident. 8 new Python tests and
1 web test; checked by hand in the browser.

## 2026-10-14: saved answers and suggestions

Repeated questions now cost nothing: a finished run whose answer passed the number check is stored
with all its steps in the console database (a volume, so it survives deploys) and replayed when
the same question comes again. Same question means the same words once case, spacing and final
punctuation are ignored, in the same mode. A saved answer is used for 7 days and only while the
data and the metric definitions are the same (a fingerprint of the data range and catalog), so a
warehouse change can never serve a stale number. Runs where the user picked a definition, or whose
answer failed the check, are never reused. "Ask again" runs the agent fresh. Suggestions show past
questions as the user types (most asked first, marked when a free saved answer exists) and metrics
named by the typed words. Checked live: 2,779 tokens the first time, 0 the second.

## 2026-10-14: an interface for analysts

The console became a working tool rather than a demo: a sidebar (Ask, History, Metrics,
Evaluations, the day's token budget as a meter), answers in three tabs (Answer; Data, with every
computed table and a CSV download each; Chart), a History view of the user's questions with status
in words and what each cost, and the metric catalog as a table with "Ask about this". Charts are
drawn in code from the computed rows, never from the model's text: one breakdown gives horizontal
bars, a period gives a line, period × group gives up to four lines with a legend and end labels,
plain totals give number tiles; anything else stays a table. The four chart colours were checked
with a colour-vision validator rather than by eye. CSV export neutralizes cells that would run as
spreadsheet formulas. Live runs moved into the volume, so History survives deploys. Two layout
bugs were found by measuring the page instead of looking at it: the answer grid had no column
width, so a wide table stretched it to 1,561px in a 727px window; and screen-reader-only header
labels escaped their scrolling tables and widened the page on phones.

## 2026-10-14: connectors

Other applications can now use the analyst, always through an API key that acts as its owner
(same budget, hourly limit and history) and cannot create or revoke keys. Two doors: a JSON
endpoint (`POST /api/v1/ask`) that returns the answer, whether it passed the number check, every
computed table and its cost, for scripts, spreadsheets and BI tools; and an MCP endpoint (`/mcp`)
with `ask_analyst` and `list_metrics`, so MCP clients get the checked analyst instead of raw
metrics. The run pipeline was refactored into one generator shared by the console's stream, the
JSON endpoint and the MCP tool, so saving, logging, limits and saved answers apply the same way
everywhere. The MCP endpoint is stateless with JSON responses (every call stands alone, so restarts
and several workers are fine) and is a plain route rather than a mount, so clients reach `/mcp`
without a redirect. One surprise: the MCP session manager can only be started once per instance, so
the endpoint is rebuilt on every application start. Checked end to end over HTTP with the official
MCP client.

## 2026-10-14: accounts, and users' own data

Accounts became invite-only from the console: an Admin page creates accounts with a one-time
password (the user must choose their own before anything else), resets passwords, changes roles
and removes accounts; everyone changes their own password.

Users can now add their own data: CSV files, a SQLite or DuckDB file, or a live PostgreSQL or
MySQL database. The design keeps the project's rule that every number comes from a governed
semantic layer: each source gets its own DuckDB workspace (CSV files loaded into it, databases
attached read-only, so live data is read where it is and never written) and a generated dbt +
MetricFlow project, built in a separate process. A prototype proved both halves first: a CSV
became a queryable semantic layer in 8 seconds, and a row added to an attached database showed up
in the next answer without a rebuild. Generated metrics are plain and say how they are computed
(rows, totals, averages, distinct counts). Each source is private to its owner, live passwords are
encrypted and passed to processes only through an environment variable, and private network
addresses are refused. The agent runs on a source without the sample shop's glossary and examples.

Found on the way: the date-range tool was hard-wired to the sample shop's order date; the planner,
without the shop's examples, skipped a "month by month" breakdown until a generic rule was added;
and a run whose caller disconnected stayed "running" in the log forever (now "stopped"). The test
client cannot simulate a dropped connection, so that fix was checked against a real server.

## 2026-10-15: a workspace for analysts, and connectors to their tools

The console was reorganized around how analysts work: one Ask page (options under the question box,
suggestions only once typing starts, earlier runs open in place), projects that group questions and
remember their data source, and a Settings page that holds everything that is not analysis (profile,
security, API keys, usage, and admin-only people and developer pages; evaluations moved there and
became admin-only on the server). Security gained two-step sign-in with an authenticator app and
one-use recovery codes, a list of sessions that can be signed out remotely, 90 days of account
activity, refusal of weak or name-based passwords, expiring API keys, and protective headers.
Charts moved to Apache ECharts (zoom, image export), loaded only when a chart is shown.

Connectors to outside tools share one safe HTTP layer (public addresses only, re-checked after every
redirect; timeouts; retries; size limits) and one encrypted credential store. Imports from a link,
any MCP server, Google, Airtable, Notion or HubSpot become data sources; answers go out as Excel or to
Slack, Teams, a new Google Sheet or an MCP tool. Found on the way: saved runs were readable by any
signed-in user who knew the id, which mattered once data could be private (now each run records its
owner); a CSV column called "day" failed to build because MetricFlow reserves time-grain names; a real
file whose headers were years (1958, 1959) had its header taken for data. Each was reproduced as a
test first.

## Interview story 40: a pause that costs nothing to resume

LangGraph's `interrupt()` has a catch that is easy to miss: when a paused run resumes, the step that
paused runs again from its first line. If the question to the user had been asked inside the
planning step, every resume would have paid for a second model call and could have produced a
different plan from the one the user was looking at. So the pause lives in its own step that
contains only code: the plan is saved by the step before it, the pause step asks the question, and
on resume it rewrites the saved plan with the chosen definition without calling the model. The test
proves it rather than assumes it: after the pause, everything is thrown away and rebuilt on the
same checkpoint file with a planner that has no scripted answer left, so any second model call
would fail the test. The same check was repeated by hand by killing the server mid-question.
*Shows: knowing a framework's resume behavior, designing steps around it, and proving durability with a test that would fail if the design were wrong.*

## Interview story 41: the run after the perfect run

After a set of fixes the development set scored 36 out of 36. One more run of a single question,
minutes later, failed: the agent said the top month had 283 new customers and that one store
supplied 493 of them. Both numbers were real, which is why the automatic check passed; the second
was that store's total for the whole year, attached to the wrong month. Two lessons came out of it.
First, 3 runs out of 3 is weak evidence for a question that fails one time in four, so the result
is now reported as a regression check with the miss next to it, not as "100%". Second, a check that
asks "does this number exist in the data?" cannot catch a true number in the wrong sentence, so the
fix went upstream: code now fetches the month-by-store breakdown whenever a plan splits the two,
and hands the writer the top month together with its own rows. The eval files also record the plan
now, because this failure had to be reconstructed from the answer text.
*Shows: distrust of a perfect score, honest reporting of a result that got worse, and fixing the cause rather than the symptom.*

## Interview story 43: one design for three kinds of data

"Let users add a database" could have meant three integrations: CSV parsing, a Postgres client, a
MySQL client, each with its own query path, and each bypassing the governed metrics the agent's
reliability depends on. Instead every source becomes the same thing: a DuckDB workspace with a
generated dbt and MetricFlow project. CSV files are loaded into it; live databases are attached
to it read-only, so DuckDB reads them in place. The agent, the checks, saved answers and the
console then work unchanged, because to them a user's source looks exactly like the sample shop.
Two prototypes settled it before any product code: a CSV queried through the existing semantic
layer class, and an attached database whose new row appeared in the next answer with no rebuild.
*Shows: reducing several integrations to one abstraction, and proving the risky part before building around it.*

## Interview story 44: the privacy hole that only appeared with a feature

Saved runs had always been readable by any signed-in user who knew a run's id. With one shared demo
dataset that was harmless, so nobody had questioned it. The day users could upload their own data, the
same route became a leak: an answer about someone's private spreadsheet could be opened by a colleague
who guessed its id, which is a timestamp and the question's first words. It surfaced while wiring the
export feature, which reads saved runs. Each run now records who asked it; it is visible to that
person, to someone who was shown it as a saved answer, and (for runs saved before owners existed)
to admins only. The rule was tested from the other user's side, not just the owner's.
*Shows: re-checking old assumptions when a new feature changes what data means, and testing access control from the outsider's side.*

## Interview story 45: the test that found what the unit tests could not

Before calling the database feature done, I connected a real public database (Rfam's MySQL) through
the console exactly as a user would. It failed three ways that 117 passing tests had missed. The
MySQL driver accepts no quoting, so any password with a space broke the connection string; the
password now travels in the driver's own environment variable and never sits in the string at all.
A `schema.table` name was cut at the dot as if it were a file extension. And the answer to "the five
largest" left out the fourth: every number was real and passed review, but the rows arrived in
alphabetical order and the writer did the ranking itself. That broke the project's own rule that code
computes and the model only writes, so breakdowns now carry a rank and arrive largest first. Each fault
was reproduced as a failing test first; the same live question then came back right.
*Shows: testing against the real thing before calling it done, and tracing a wrong answer back to a broken design rule rather than a prompt.*

## Interview story 46: bring your own model without forking the agent

Users asked to run their questions on their own Claude, GPT or Gemini key. The agent already took its
planner and writer as two functions, so a key only has to produce those two functions: everything after
(execution, the computed facts, the number check) is unchanged and no less safe on another model. The
differences live in one small table: how each provider is asked for structured output (current Claude
models refuse a forced tool call, so they get the provider's JSON schema first), which models a key can
list, and how the key is sent. A key is checked by the provider before it is stored, encrypted, and
never shown again; questions on it skip the shared free-tier budget but keep the hourly limit, which
protects the server rather than the quota.
*Shows: designing seams early so a large feature becomes a small one, and separating whose cost a limit protects.*

## Interview story 47: least privilege is the normal case, not the edge case

The PostgreSQL test used the kind of account a careful company hands out: read-only, and allowed to
read one table but not its neighbour. The build failed outright with "permission denied", because
the database lists every table in a schema whether or not you may read it. The first instinct, "tell
users to grant more", is the wrong one: an analyst tool should work with the least access it is given.
Each table is now profiled on its own; one that cannot be read is skipped and the source says which
and why, in the database's own words rather than the internal query. The same run showed the planner
writing `amount_total - amount_total` for "highest minus lowest", a column of zeros that the writer
happened to ignore; plans with a calculation that cannot vary are now refused and pointed to the gap
and rank fields code already computes.
*Shows: designing for the restricted accounts real companies use, and closing a hole that had not yet caused a wrong answer.*

## Interview story 48: proving a translation instead of trusting it

Analysts wanted each answer's query in their own language: SQL Server, Oracle, MySQL, BigQuery, even
MongoDB. A transpiler library does most of this, so the risk was trusting it. Reading its output engine
by engine showed where it goes quietly wrong: Oracle and SQLite were given a BigQuery function, MySQL
counted months from year 0 (rejected in strict mode), and BigQuery and SQL Server weeks started on
Sunday while the answers used Monday weeks, so a weekly report would silently disagree. Those parts are
now written by hand from each engine's documentation, dates are written so no session setting can change
them, and a user's generated views are inlined so the query runs on their own tables. MongoDB pipelines
are built from the metric definitions, and anything without an exact equivalent is refused with the
reason. Precision is tested by running translations: the SQLite version on a copy of the warehouse
returns DuckDB's rows exactly, and a live answer's PostgreSQL version, run on that PostgreSQL, returned
the same numbers.
*Shows: verifying generated code by executing it, and refusing to approximate where a wrong answer would look right.*

## Interview story 49: a chart that hid a year

The live dashboard was tested by inserting three orders dated today into a database whose other orders
ended a year earlier. The new orders appeared, but the monthly line went straight from June 2025 to
October 2026: the query returns only months that have rows, so fifteen empty months vanished and the
line suggested continuity. Empty periods are now filled, with 0 for totals and counts (true) and no
value for averages and ratios (an average of nothing is not zero). The same distinction decides whether
a breakdown shows shares, because a share of an average means nothing.
*Shows: testing with awkward data instead of tidy data, and knowing when zero is a fact and when it is a lie.*

## Interview story 50: an expert agent that costs nothing per question

The request was "another agent who thinks like an expert salesperson". The obvious build is a second
model call per question; on a free tier of 200K tokens a day that halves the questions the console can
answer, and its advice would vary from run to run. What an expert actually adds here is knowledge, not
reasoning: that "basket size" and "panier moyen" mean average order value, that average order value is a
ratio and must not be summed, that a rise in costs is bad news, that a comparison is best shown as a
waterfall. That fits in a reviewed knowledge file applied by code, as an agent step of its own: it costs
no tokens, gives the same advice every time, can be tested, and its direction ("lower is better") feeds
the colours, the dashboard and the writer, so an answer no longer calls a fall in costs a decline.
*Shows: choosing the cheapest mechanism that delivers the capability, and making domain knowledge a reviewable artefact.*
