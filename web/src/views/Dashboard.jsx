import { useCallback, useEffect, useState } from "react";

import BarChart from "../components/charts/BarChart";
import LineChart from "../components/charts/LineChart";
import SourcePicker, { useSource } from "../components/SourcePicker";
import { getJSON } from "../lib/api";
import { formatCell, formatNumber } from "../lib/format";
import { dimensionLabel } from "../lib/results";

const REFRESH_SECONDS = 60; // live databases only; the server keeps results for 30 seconds

function shown(tile, value) {
  if (value === null || value === undefined) return "no data";
  return tile.percent ? `${(value * 100).toFixed(1)}%` : formatNumber(value);
}

/** One headline number: the last 30 days, with the change against the 30 days before. */
function Tile({ tile, window }) {
  const up = tile.change_pct > 0;
  const change =
    tile.change_pct === null || tile.change_pct === undefined ? null : (
      <span className={`text-sm font-medium ${up ? "text-code" : tile.change_pct < 0 ? "text-fail" : "text-muted"}`}>
        <span aria-hidden>{up ? "▲" : tile.change_pct < 0 ? "▼" : "■"}</span> {Math.abs(tile.change_pct)}%
      </span>
    );
  return (
    <div className="space-y-1 rounded-xl border border-rule bg-surface p-4" title={tile.description}>
      <p className="text-sm text-muted">{tile.label}</p>
      <p className="text-2xl font-semibold tracking-tight">{shown(tile, tile.value)}</p>
      <p className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted">
        {change}
        <span>
          vs {shown(tile, tile.previous)} in the {window.days} days before
        </span>
      </p>
    </div>
  );
}

/** Headline numbers, a trend and breakdowns for one source, computed without the model (no tokens). */
export default function Dashboard() {
  const source = useSource();
  const [board, setBoard] = useState(null);
  const [error, setError] = useState("");
  const [paused, setPaused] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    const scope = source === "sample" ? "" : `?source=${encodeURIComponent(source)}`;
    return getJSON(`/api/dashboard${scope}`)
      .then((data) => {
        setBoard(data);
        setError("");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [source]);

  useEffect(() => {
    setBoard(null);
    load();
  }, [load]);
  useEffect(() => {
    if (!board?.live || paused) return undefined;
    const timer = setInterval(load, REFRESH_SECONDS * 1000);
    return () => clearInterval(timer);
  }, [board?.live, paused, load]);

  const updated = board ? new Date(board.updated_at * 1000).toLocaleTimeString() : "";
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SourcePicker />
        {board && (
          <div className="flex flex-wrap items-center gap-3 text-sm text-muted">
            {board.live ? (
              <span className="flex items-center gap-1.5 text-code">
                <span aria-hidden className={`h-2 w-2 rounded-full bg-code ${paused ? "" : "animate-pulse"}`} />
                Live · updated {updated}
              </span>
            ) : (
              <span>
                Data from {board.range.first} to {board.range.last}
              </span>
            )}
            {board.live && (
              <button type="button" onClick={() => setPaused((p) => !p)} className="underline hover:text-ink">
                {paused ? "Resume updates" : "Pause updates"}
              </button>
            )}
            <button type="button" onClick={load} disabled={loading} className="rounded-md border border-rule px-2.5 py-1 hover:border-ink disabled:opacity-50">
              {loading ? "Refreshing…" : "Refresh"}
            </button>
          </div>
        )}
      </div>
      {error && <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>}
      {!board && !error && <p className="text-muted">Computing the dashboard…</p>}
      {board && (
        <>
          <section aria-label="Headline numbers" className="space-y-2">
            <p className="text-sm text-muted">
              Last {board.window.days} days of data ({board.window.from} to {board.window.to})
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {board.tiles.map((tile) => (
                <Tile key={tile.metric} tile={tile} window={board.window} />
              ))}
            </div>
          </section>
          {board.trend?.points.length > 0 && (
            <section aria-label="Trend" className="space-y-2 rounded-xl border border-rule bg-surface p-4">
              <LineChart points={board.trend.points} metricLabel={board.trend.label} timeDimension={board.trend.dimension} />
            </section>
          )}
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {board.breakdowns.map((b) => (
              <section key={b.dimension} aria-label={`By ${dimensionLabel(b.dimension)}`} className="space-y-2 rounded-xl border border-rule bg-surface p-4">
                <BarChart points={b.points} metricLabel={b.label} dimension={b.dimension} />
                <table className="w-full text-sm">
                  <caption className="sr-only">
                    {b.label} by {dimensionLabel(b.dimension)}, all dates
                  </caption>
                  <tbody>
                    {b.points.map((p) => (
                      <tr key={p.x} className="border-t border-rule">
                        <td className="py-1">{formatCell(b.dimension, p.x)}</td>
                        <td className="py-1 text-right tabular-nums">{formatNumber(p.value)}</td>
                        <td className="w-16 py-1 text-right text-muted tabular-nums">{p.share_pct != null ? `${p.share_pct}%` : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="text-xs text-muted">
                  All dates{b.others ? ` · ${b.others} smaller groups not shown` : ""}
                </p>
              </section>
            ))}
          </div>
          {board.errors.length > 0 && <p className="text-xs text-muted">Some parts could not be computed: {board.errors.join("; ")}</p>}
        </>
      )}
    </div>
  );
}
