import { useEffect, useState } from "react";

import { getJSON } from "../lib/api";
import { formatNumber } from "../lib/format";
import { tone, useSigns } from "../lib/signs";
import Sparkline from "./charts/Sparkline";

/** The dashboard's headline numbers on the Ask page, so they are in sight before any question is asked. */
export default function GlanceStrip({ source }) {
  const [board, setBoard] = useState(null);
  const signs = useSigns();
  useEffect(() => {
    let current = true;
    setBoard(null);
    const scope = source === "sample" ? "" : `?source=${encodeURIComponent(source)}`;
    getJSON(`/api/dashboard${scope}`)
      .then((data) => current && setBoard(data))
      .catch(() => current && setBoard(null)); // the dashboard page says why; here it simply stays out of the way
    return () => {
      current = false;
    };
  }, [source]);
  if (!board?.tiles?.length) return null;
  return (
    <section aria-label="At a glance" className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium text-muted">
          At a glance · last {board.window.days} days of data{board.live ? " · live" : ""}
        </h2>
        <a href="#dashboard" className="text-sm text-brand hover:underline">
          Open the dashboard
        </a>
      </div>
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {board.tiles.map((t) => (
          <a key={t.metric} href="#dashboard" className="space-y-1 rounded-xl border border-rule bg-surface p-3 transition-shadow hover:shadow-sm" title={t.description}>
            <p className="truncate text-xs text-muted">{t.label}</p>
            <div className="flex items-end justify-between gap-2">
              <p className="text-lg font-semibold">{t.value == null ? "no data" : t.percent ? `${(t.value * 100).toFixed(1)}%` : formatNumber(t.value)}</p>
              <Sparkline values={t.spark ?? []} label={`${t.label}, recent trend`} width={64} height={22} />
            </div>
            {t.change_pct != null && (
              <p className={`text-xs font-medium ${tone(signs, { [t.metric]: t.higher_is_better }, "change", t.change_pct, t.metric) || "text-muted"}`}>
                {t.change_pct > 0 ? "▲" : t.change_pct < 0 ? "▼" : "■"} {Math.abs(t.change_pct)}%
              </p>
            )}
          </a>
        ))}
      </div>
    </section>
  );
}
