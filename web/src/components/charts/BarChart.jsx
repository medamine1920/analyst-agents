import { useState } from "react";

import { formatCell, formatNumber } from "../../lib/format";
import { dimensionLabel } from "../../lib/results";
import { compact, niceTicks, shorten } from "../../lib/scale";

const BAR = 20; // thickness: thin marks, never filling the row
const ROW = 32;
const LABEL_W = 132;
const VALUE_W = 72;
const WIDTH = 640;

/** Horizontal bars for one metric across the groups of one breakdown, largest first. */
export default function BarChart({ points, metricLabel, dimension }) {
  const [active, setActive] = useState(null);
  const max = Math.max(...points.map((p) => p.value), 0);
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1] || 1;
  const plotW = WIDTH - LABEL_W - VALUE_W;
  const height = points.length * ROW + 28;
  const x = (value) => LABEL_W + (Math.max(value, 0) / top) * plotW;

  return (
    <figure className="space-y-2">
      <figcaption className="text-sm text-muted">
        {metricLabel} by {dimensionLabel(dimension)}
      </figcaption>
      <div className="relative">
        <svg viewBox={`0 0 ${WIDTH} ${height}`} className="w-full" role="img" aria-label={`${metricLabel} by ${dimensionLabel(dimension)}`}>
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={x(tick)} x2={x(tick)} y1={0} y2={points.length * ROW} className="stroke-chart-grid" strokeWidth="1" />
              <text x={x(tick)} y={points.length * ROW + 18} textAnchor="middle" className="fill-chart-muted text-[11px] tabular-nums">
                {compact(tick)}
              </text>
            </g>
          ))}
          <line x1={LABEL_W} x2={LABEL_W} y1={0} y2={points.length * ROW} className="stroke-chart-axis" strokeWidth="1" />
          {points.map((point, index) => {
            const y = index * ROW + (ROW - BAR) / 2;
            const end = x(point.value);
            const r = Math.min(4, end - LABEL_W);
            // Rounded at the data end, square at the baseline.
            const path = `M${LABEL_W},${y} H${end - r} Q${end},${y} ${end},${y + r} V${y + BAR - r} Q${end},${y + BAR} ${end - r},${y + BAR} H${LABEL_W} Z`;
            const show = () => setActive({ ...point, top: index * ROW });
            return (
              <g
                key={point.x}
                tabIndex={0}
                onPointerEnter={show}
                onFocus={show}
                onPointerLeave={() => setActive(null)}
                onBlur={() => setActive(null)}
                className="outline-none"
                aria-label={`${point.x}: ${formatNumber(point.value)}`}
              >
                <rect x={0} y={index * ROW} width={WIDTH} height={ROW} fill="transparent" />
                <text x={LABEL_W - 10} y={index * ROW + ROW / 2 + 4} textAnchor="end" className="fill-ink text-[12px]">
                  {shorten(formatCell(dimension, point.x))}
                </text>
                <path
                  d={path}
                  className={`fill-series-1 transition-opacity ${active && active.x !== point.x ? "opacity-60" : ""}`}
                />
                <text x={end + 6} y={index * ROW + ROW / 2 + 4} className="fill-ink-2 text-[12px] tabular-nums">
                  {compact(point.value)}
                </text>
              </g>
            );
          })}
        </svg>
        {active && (
          <div
            className="pointer-events-none absolute right-0 rounded-md border border-rule bg-surface px-2.5 py-1.5 text-sm shadow-sm"
            style={{ top: `${(active.top / height) * 100}%` }}
            role="status"
          >
            <strong className="block tabular-nums">{formatNumber(active.value)}</strong>
            <span className="text-muted">{formatCell(dimension, active.x)}</span>
          </div>
        )}
      </div>
    </figure>
  );
}
