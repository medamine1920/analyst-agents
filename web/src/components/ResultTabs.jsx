import { useMemo, useState } from "react";

import { downloadCsv } from "../lib/csv";
import { CHART_NAMES, chartPoints, datasets, isTimeDimension, latestAdvice } from "../lib/results";
import { PolarityContext } from "../lib/signs";
import { DataTable } from "./bits";
import BarChart from "./charts/BarChart";
import HeatmapChart from "./charts/HeatmapChart";
import LineChart from "./charts/LineChart";
import StatTiles from "./charts/StatTiles";
import TreemapChart from "./charts/TreemapChart";
import WaterfallChart from "./charts/WaterfallChart";
import ExpertNotes from "./ExpertNotes";
import QueryView from "./QueryView";

const TABS = [
  ["answer", "Answer"],
  ["data", "Data"],
  ["chart", "Chart"],
  ["query", "Query"],
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

function Chart({ set, kind, metric, label }) {
  const time = set.groupBy.find(isTimeDimension);
  const category = set.groupBy.find((g) => !isTimeDimension(g));
  const points = chartPoints({ ...set, chart: kind }, metric);
  if (kind === "tiles") return <StatTiles row={set.rows[0]} metrics={set.metrics} labels={{ [metric]: label }} />;
  if (kind === "bar") return <BarChart points={points} metricLabel={label} dimension={category ?? time} />;
  if (kind === "treemap") return <TreemapChart points={points} metricLabel={label} dimension={category} />;
  if (kind === "heatmap") return <HeatmapChart points={points} metricLabel={label} timeDimension={time} seriesDimension={category} />;
  if (kind === "waterfall") {
    const rows = set.rows.filter((r) => r.metric === metric && "change" in r);
    return (
      <WaterfallChart rows={rows} metric={metric} metricLabel={label} dimension={set.groupBy[0]}
        beforeLabel={set.comparison.before} afterLabel={set.comparison.after} />
    );
  }
  const variant = kind === "area" ? "area" : kind === "stacked" ? "stacked" : "line";
  return (
    <LineChart points={points} metricLabel={label} timeDimension={time}
      seriesDimension={kind === "lines" || kind === "stacked" ? category : null} variant={variant} />
  );
}

function ChartView({ sets, labels }) {
  const chartable = sets.filter((s) => s.chart);
  // Open on the most telling chart: a breakdown, a trend or a waterfall before plain totals.
  const [setId, setSetId] = useState((chartable.find((s) => s.chart !== "tiles") ?? chartable[0])?.id);
  const set = chartable.find((s) => s.id === setId) ?? chartable[0];
  const [metric, setMetric] = useState(null);
  const [kinds, setKinds] = useState({}); // the reader's choice of chart, per result
  if (!set) return <p className="text-muted">These results are easier to read as a table: see the Data tab.</p>;
  const kind = set.options?.includes(kinds[set.id]) ? kinds[set.id] : set.chart;
  const shownMetric = set.metrics.includes(metric) ? metric : set.metrics[0];
  const label = labels[shownMetric] ?? shownMetric;
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
        {kind !== "tiles" && set.metrics.length > 1 && (
          <label className="flex items-center gap-2">
            <span className="text-muted">Metric</span>
            <select value={shownMetric} onChange={(e) => setMetric(e.target.value)} className="rounded-md border border-rule bg-surface px-2 py-1">
              {set.metrics.map((m) => (
                <option key={m} value={m}>{labels[m] ?? m}</option>
              ))}
            </select>
          </label>
        )}
        {set.options?.length > 1 && (
          <label className="flex items-center gap-2">
            <span className="text-muted">Show as</span>
            <select value={kind} onChange={(e) => setKinds((k) => ({ ...k, [set.id]: e.target.value }))} className="rounded-md border border-rule bg-surface px-2 py-1">
              {set.options.map((k) => (
                <option key={k} value={k}>
                  {CHART_NAMES[k]}
                  {k === set.chart && set.why ? " (expert's pick)" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <Chart key={`${set.id}-${kind}-${shownMetric}`} set={set} kind={kind} metric={shownMetric} label={label} />
      <p className="text-xs text-muted">
        {kind === set.chart && set.why ? `Why this chart: ${set.why} ` : ""}The same numbers are in the Data tab, with a CSV download.
      </p>
    </div>
  );
}

/** The answer, the numbers behind it and a chart of them, one tab each. */
export default function ResultTabs({ steps, labels, source = "sample", onAsk, children }) {
  const [tab, setTab] = useState("answer");
  const sets = useMemo(() => datasets(steps), [steps]);
  const advice = useMemo(() => latestAdvice(steps), [steps]);
  // Which way is good for each metric (the sales expert's reading), for colouring gains and losses.
  const polarity = useMemo(
    () => Object.fromEntries((advice?.definitions ?? []).map((d) => [d.metric, d.higher_is_better])),
    [advice],
  );
  const available = sets.length ? TABS : TABS.slice(0, 1);
  return (
    <PolarityContext.Provider value={polarity}>
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
          {tab === "answer" && (
            <div className="space-y-6">
              {children}
              {sets.some((s) => s.chart && s.chart !== "tiles") && (
                <div className="rounded-xl border border-rule bg-surface p-4">
                  <ChartView sets={sets} labels={labels} />
                </div>
              )}
              {advice && <ExpertNotes advice={advice} onAsk={onAsk} />}
            </div>
          )}
          {tab === "data" && <DataView sets={sets} />}
          {tab === "chart" && <ChartView sets={sets} labels={labels} />}
          {tab === "query" && <QueryView sets={sets.filter((s) => s.step)} source={source} />}
        </div>
      </div>
    </PolarityContext.Provider>
  );
}
