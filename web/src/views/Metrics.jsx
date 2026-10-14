import { useEffect, useMemo, useState } from "react";

import SourcePicker, { useSource } from "../components/SourcePicker";
import { Ident } from "../components/bits";
import { getJSON } from "../lib/api";
import { linkTo } from "../lib/route";

const TYPE_NOTES = {
  simple: "Sum or count",
  ratio: "Ratio",
  derived: "Formula",
  cumulative: "Running total",
};

/** The governed catalog as a table: what each metric means and what it can be broken down by. */
export default function Metrics() {
  const [data, setData] = useState(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const source = useSource();
  useEffect(() => {
    setData(null);
    const scope = source === "sample" ? "" : `?source=${encodeURIComponent(source)}`;
    getJSON(`/api/metrics${scope}`).then(setData).catch((e) => setError(e.message));
  }, [source]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const metrics = data?.metrics ?? [];
    if (!q) return metrics;
    return metrics.filter((m) => `${m.name} ${m.label} ${m.description}`.toLowerCase().includes(q));
  }, [data, query]);

  if (error) return <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-fail">{error}</p>;
  if (!data) return <p className="text-muted">Loading the metric catalog…</p>;
  return (
    <div className="space-y-4">
      <SourcePicker />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <p className="max-w-2xl text-sm text-muted">
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
      {shown.length === 0 ? (
        <p className="text-muted">No metric matches “{query}”. Try a word like orders, tax or customers.</p>
      ) : (
        <div className="relative overflow-x-auto rounded-xl border border-rule bg-surface">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="text-left text-muted">
                <th className="px-3 py-2 font-medium">Metric</th>
                <th className="px-3 py-2 font-medium">Definition</th>
                <th className="px-3 py-2 font-medium">Can be broken down by</th>
                <th className="px-3 py-2 font-medium"><span className="sr-only">Ask</span></th>
              </tr>
            </thead>
            <tbody>
              {shown.map((metric) => (
                <tr key={metric.name} className="border-t border-rule align-top">
                  <td className="px-3 py-2.5">
                    <p className="font-medium">{metric.label}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                      <Ident>{metric.name}</Ident>
                      <span>{TYPE_NOTES[metric.type] ?? metric.type}</span>
                    </p>
                  </td>
                  <td className="max-w-sm px-3 py-2.5">{metric.description}</td>
                  <td className="px-3 py-2.5">
                    <p className="flex flex-wrap gap-1">
                      {metric.group_by.map((g) => (
                        <Ident key={g}>{g}</Ident>
                      ))}
                    </p>
                  </td>
                  <td className="px-3 py-2.5 text-right whitespace-nowrap">
                    <a href={linkTo("ask", { mode: "live", q: `${metric.label} ` })} className="font-medium underline">
                      Ask about this
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
