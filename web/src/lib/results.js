/**
 * Turn a run's steps into datasets an analyst can read: one per query, with its rows, the metrics it
 * measured and the breakdown it used, and the chart that fits it (if any). Pure functions: no fetching.
 */

const TIME_GRAINS = ["day", "week", "month", "quarter", "year"];
// Past this many groups a multi-line chart stops being readable: the table says it better.
export const MAX_SERIES = 4;

/** "store__store_name" -> "store name", "metric_time__month" -> "month": for captions, not for queries. */
export function dimensionLabel(name) {
  return (name ?? "").split("__").pop().replaceAll("_", " ");
}

export function isTimeDimension(name) {
  const last = name.split("__").pop();
  return name.startsWith("metric_time") || TIME_GRAINS.includes(last) || last.endsWith("_at");
}

/** The plan the queries actually ran with: the clarify step's version when the user picked a definition. */
function latestPlan(steps) {
  const withPlan = steps.filter((s) => (s.node === "plan" || s.node === "clarify") && s.data?.plan);
  return withPlan.length ? withPlan[withPlan.length - 1].data.plan : null;
}

function latestFacts(steps) {
  const analyzed = steps.filter((s) => s.node === "analyze" || s.node === "analyze_partial");
  return analyzed.length ? analyzed[analyzed.length - 1].data?.facts ?? null : null;
}

function numericColumns(rows) {
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  return columns.filter((c) => rows.some((row) => typeof row[c] === "number"));
}

function chartKind(groupBy, rows) {
  if (!rows.length) return null;
  if (groupBy.length === 0) return "tiles";
  if (groupBy.length === 1) return isTimeDimension(groupBy[0]) ? (rows.length > 1 ? "line" : "bar") : "bar";
  if (groupBy.length === 2) {
    const time = groupBy.find(isTimeDimension);
    const category = groupBy.find((g) => !isTimeDimension(g));
    if (time && category && new Set(rows.map((r) => r[category])).size <= MAX_SERIES) return "lines";
  }
  return null;
}

/** The SQL each query ran with, by step id (kept with the run; never shown to the model). */
function latestSql(steps) {
  const runs = steps.filter((s) => s.node === "execute" && s.data?.queries);
  const queries = runs.length ? runs[runs.length - 1].data.queries : {};
  return Object.fromEntries(Object.entries(queries).map(([id, q]) => [id, q.sql ?? null]));
}

/** One dataset per query step that returned rows, in plan order. */
export function datasets(steps) {
  const facts = latestFacts(steps);
  if (!facts?.steps) return [];
  const planSteps = Object.fromEntries((latestPlan(steps)?.steps ?? []).map((s) => [s.id, s]));
  const sql = latestSql(steps);
  return facts.steps
    .filter((step) => step.rows?.length)
    .map((step) => {
      const planned = planSteps[step.id];
      const numeric = numericColumns(step.rows);
      const groupBy = planned?.group_by ?? Object.keys(step.rows[0]).filter((c) => !numeric.includes(c));
      // The governed metrics first; computed columns (shares, gaps, formulas) stay in the table.
      const metrics = (planned?.metrics ?? numeric).filter((m) => numeric.includes(m));
      return {
        id: step.id,
        title: step.purpose || step.id,
        period: Array.isArray(step.period) ? step.period.filter(Boolean).join(" to ") || "all dates" : step.period,
        rows: step.rows,
        totals: step.totals ?? null,
        truncated: Boolean(step.truncated),
        groupBy,
        metrics: metrics.length ? metrics : numeric,
        chart: chartKind(groupBy, step.rows),
        sql: sql[step.id] ?? null,
        step: planned ?? null,
      };
    });
}

/** Rows for one chart: [{ x, value, series }], sorted the way the chart reads. */
export function chartPoints(dataset, metric) {
  const time = dataset.groupBy.find(isTimeDimension);
  const category = dataset.groupBy.find((g) => !isTimeDimension(g));
  const points = dataset.rows
    .filter((row) => typeof row[metric] === "number")
    .map((row) => ({
      x: String(row[time ?? category] ?? ""),
      value: row[metric],
      series: time && category ? String(row[category]) : null,
    }));
  if (dataset.chart === "bar") return points.sort((a, b) => b.value - a.value);
  return points.sort((a, b) => a.x.localeCompare(b.x));
}
