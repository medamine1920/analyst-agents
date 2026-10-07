import { useEffect, useMemo, useState } from "react";

import { Ident } from "../components/bits";
import { getJSON } from "../lib/api";

const TYPE_NOTES = {
  simple: "a sum or count",
  ratio: "a ratio of two metrics",
  derived: "a formula over other metrics",
  cumulative: "a running total",
};

export default function Metrics() {
  const [data, setData] = useState(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    getJSON("/api/metrics").then(setData).catch((e) => setError(e.message));
  }, []);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const metrics = data?.metrics ?? [];
    if (!q) return metrics;
    return metrics.filter((m) => `${m.name} ${m.label} ${m.description}`.toLowerCase().includes(q));
  }, [data, query]);

  if (error) return <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-fail">{error}</p>;
  if (!data) return <p className="text-muted">Loading the metric catalog…</p>;
  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <p className="max-w-2xl text-muted">
          The agent can only use these {data.metrics.length} governed metrics. Data runs from {data.data_range.first_date} to{" "}
          {data.data_range.last_date}.
        </p>
        <label className="block">
          <span className="sr-only">Search metrics</span>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search, e.g. revenue"
            className="w-full rounded-lg border border-rule bg-surface px-3 py-2 sm:w-72"
          />
        </label>
      </div>
      {shown.length === 0 && <p className="text-muted">No metric matches “{query}”. Try a word like orders, tax or customers.</p>}
      <ul className="divide-y divide-rule rounded-xl border border-rule bg-surface">
        {shown.map((metric) => (
          <li key={metric.name} className="grid gap-2 p-4 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] md:gap-6">
            <div className="space-y-1">
              <h2 className="font-display text-lg font-semibold">{metric.label}</h2>
              <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
                <Ident>{metric.name}</Ident>
                <span>{TYPE_NOTES[metric.type] ?? metric.type}</span>
              </p>
              <p className="text-[0.95rem]">{metric.description}</p>
            </div>
            <div>
              <p className="mb-1.5 text-sm text-muted">Can be broken down by</p>
              <p className="flex flex-wrap gap-1.5">
                {metric.group_by.map((g) => (
                  <Ident key={g}>{g}</Ident>
                ))}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
