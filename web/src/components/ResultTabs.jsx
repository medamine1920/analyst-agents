import { useMemo, useState } from "react";

import { downloadCsv } from "../lib/csv";
import { chartPoints, datasets, isTimeDimension } from "../lib/results";
import { DataTable } from "./bits";
import BarChart from "./charts/BarChart";
import LineChart from "./charts/LineChart";
import StatTiles from "./charts/StatTiles";

const TABS = [
  ["answer", "Answer"],
  ["data", "Data"],
  ["chart", "Chart"],
];

function DataView({ sets }) {
  return (
    <div className="space-y-6">
      {sets.map((set) => (
        <section key={set.id} className="space-y-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="font-semibold">{set.title}</h3>
            <button
              type="button"
              onClick={() => downloadCsv(set.rows, set.id)}
              className="rounded-md border border-rule px-2.5 py-1 text-sm hover:border-ink"
            >
              Download CSV
            </button>
          </div>
          <p className="text-xs text-muted">
            {set.rows.length} {set.rows.length === 1 ? "row" : "rows"} · {set.period}
            {set.truncated ? " · only the first rows were kept for the answer" : ""}
          </p>
          <DataTable rows={set.rows} limit={50} />
          {set.totals && (
            <>
              <p className="text-xs text-muted">Totals across all groups</p>
              <DataTable rows={set.totals} />
            </>
          )}
        </section>
      ))}
    </div>
  );
}

function ChartView({ sets, labels }) {
  const chartable = sets.filter((s) => s.chart);
  // Open on the most telling chart: a breakdown or a trend before plain totals.
  const [setId, setSetId] = useState((chartable.find((s) => s.chart !== "tiles") ?? chartable[0])?.id);
  const set = chartable.find((s) => s.id === setId) ?? chartable[0];
  const [metric, setMetric] = useState(null);
  if (!set) return <p className="text-muted">These results are easier to read as a table: see the Data tab.</p>;
  const shownMetric = set.metrics.includes(metric) ? metric : set.metrics[0];
  const label = labels[shownMetric] ?? shownMetric;
  const time = set.groupBy.find(isTimeDimension);
  const category = set.groupBy.find((g) => !isTimeDimension(g));
  const points = chartPoints(set, shownMetric);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3 text-sm">
        {chartable.length > 1 && (
          <label className="flex items-center gap-2">
            <span className="text-muted">Result</span>
            <select value={set.id} onChange={(e) => setSetId(e.target.value)} className="rounded-md border border-rule bg-surface px-2 py-1">
              {chartable.map((s) => (
                <option key={s.id} value={s.id}>{s.title}</option>
              ))}
            </select>
          </label>
        )}
        {set.chart !== "tiles" && set.metrics.length > 1 && (
          <label className="flex items-center gap-2">
            <span className="text-muted">Metric</span>
            <select value={shownMetric} onChange={(e) => setMetric(e.target.value)} className="rounded-md border border-rule bg-surface px-2 py-1">
              {set.metrics.map((m) => (
                <option key={m} value={m}>{labels[m] ?? m}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      {set.chart === "tiles" && <StatTiles row={set.rows[0]} metrics={set.metrics} labels={labels} />}
      {set.chart === "bar" && <BarChart points={points} metricLabel={label} dimension={category ?? time} />}
      {(set.chart === "line" || set.chart === "lines") && (
        <LineChart points={points} metricLabel={label} timeDimension={time} seriesDimension={set.chart === "lines" ? category : null} />
      )}
      <p className="text-xs text-muted">The same numbers are in the Data tab, with a CSV download.</p>
    </div>
  );
}

/** The answer, the numbers behind it and a chart of them, one tab each. */
export default function ResultTabs({ steps, labels, children }) {
  const [tab, setTab] = useState("answer");
  const sets = useMemo(() => datasets(steps), [steps]);
  const available = sets.length ? TABS : TABS.slice(0, 1);
  return (
    <div className="space-y-4">
      {available.length > 1 && (
        <div role="tablist" aria-label="Result views" className="flex gap-1 border-b border-rule">
          {available.map(([id, text]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`-mb-px border-b-2 px-3 py-1.5 text-sm font-medium ${tab === id ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink"}`}
            >
              {text}
              {id === "data" && <span className="ml-1.5 text-xs text-muted">{sets.length}</span>}
            </button>
          ))}
        </div>
      )}
      <div role="tabpanel">
        {tab === "answer" && children}
        {tab === "data" && <DataView sets={sets} />}
        {tab === "chart" && <ChartView sets={sets} labels={labels} />}
      </div>
    </div>
  );
}
