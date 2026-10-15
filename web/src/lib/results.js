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

/** The sales expert's notes on the run (definitions, a chart per result, follow-ups), if it gave any. */
export function latestAdvice(steps) {
  const advised = steps.filter((s) => s.node === "advise" && s.data?.advice);
  return advised.length ? advised[advised.length - 1].data.advice : null;
}

export const CHART_NAMES = {
  tiles: "Numbers",
  bar: "Bars",
  treemap: "Treemap (shares)",
  line: "Line",
  area: "Area",
  lines: "Lines",
  stacked: "Stacked area",
  heatmap: "Heatmap",
  waterfall: "Waterfall (what changed)",
};

/** The charts that can show a result truthfully, best default first. */
export function chartOptions(groupBy, rows) {
  if (!rows.length) return [];
  if (groupBy.length === 0) return ["tiles"];
  const time = groupBy.find(isTimeDimension);
  const category = groupBy.find((g) => !isTimeDimension(g));
  if (groupBy.length === 1) return time ? (rows.length > 1 ? ["line", "area"] : ["bar"]) : ["bar", "treemap"];
  if (groupBy.length === 2 && time && category) {
    // Past four groups lines tangle and the palette runs out: a heatmap shows any number of them.
    return new Set(rows.map((r) => r[category])).size <= MAX_SERIES ? ["lines", "stacked", "heatmap"] : ["heatmap"];
  }
  return [];
}

function chartKind(groupBy, rows) {
  return chartOptions(groupBy, rows)[0] ?? null;
}

const EXPERT_KINDS = { stacked_area: "stacked" }; // the expert's names for the console's chart kinds

/** Comparisons (period against period) as datasets of their own: the waterfall shows what moved the total. */
function comparisonSets(facts, advice) {
  return (facts.comparisons ?? [])
    .filter((c) => c.rows?.some((r) => "change" in r))
    .map((c) => {
      const id = `${c.before}->${c.after}`;
      const metric = c.rows[0].metric;
      const groupBy = Object.keys(c.rows[0]).filter(
        (k) => !["metric", "before", "after", "change", "change_pct", "share_of_total_change_pct"].includes(k),
      );
      const waterfall = advice?.charts?.[id]?.kind === "waterfall" && groupBy.length === 1;
      return {
        id,
        title: `What changed from ${c.before} to ${c.after}`,
        period: `${c.before} to ${c.after}`,
        rows: c.rows,
        totals: null,
        truncated: false,
        groupBy,
        metrics: [metric],
        chart: waterfall ? "waterfall" : null,
        options: waterfall ? ["waterfall"] : [],
        comparison: { before: c.before, after: c.after, metric },
        why: advice?.charts?.[id]?.why ?? null,
        sql: null,
        step: null,
      };
    });
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
  const advice = latestAdvice(steps);
  const sets = facts.steps
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
        sql: sql[step.id] ?? null,
        step: planned ?? null,
        ...expertChart(groupBy, step.rows, advice?.charts?.[step.id]),
      };
    });
  return [...sets, ...comparisonSets(facts, advice)];
}

/** The chart kinds for a result, with the sales expert's pick as the default when it is one of them. */
function expertChart(groupBy, rows, pick) {
  const options = chartOptions(groupBy, rows);
  const wanted = pick ? EXPERT_KINDS[pick.kind] ?? pick.kind : null;
  return {
    options,
    chart: options.includes(wanted) ? wanted : chartKind(groupBy, rows),
    why: options.includes(wanted) ? pick.why : null,
  };
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
  if (dataset.chart === "bar" || dataset.chart === "treemap") return points.sort((a, b) => b.value - a.value);
  return points.sort((a, b) => a.x.localeCompare(b.x));
}
