import { useRef, useState } from "react";

import { formatCell, formatNumber } from "../../lib/format";
import { dimensionLabel } from "../../lib/results";
import { compact, niceTicks } from "../../lib/scale";

const WIDTH = 640;
const HEIGHT = 240;
const PAD = { top: 12, right: 96, bottom: 28, left: 48 };
// Written out in full: Tailwind only generates classes it can find as whole words in the source.
const SERIES = [
  { stroke: "stroke-series-1", fill: "fill-series-1", key: "bg-series-1" },
  { stroke: "stroke-series-2", fill: "fill-series-2", key: "bg-series-2" },
  { stroke: "stroke-series-3", fill: "fill-series-3", key: "bg-series-3" },
  { stroke: "stroke-series-4", fill: "fill-series-4", key: "bg-series-4" },
];

/**
 * A metric over time: one line, or one per group (at most four). Series keep their colour by name,
 * sorted alphabetically, so a store is always the same colour. The crosshair snaps to the nearest
 * period and lists every series there.
 */
export default function LineChart({ points, metricLabel, timeDimension, seriesDimension }) {
  const [hover, setHover] = useState(null);
  const svgRef = useRef(null);
  const xs = [...new Set(points.map((p) => p.x))].sort();
  const names = [...new Set(points.map((p) => p.series ?? metricLabel))].sort();
  const lines = names.map((name, index) => ({
    name,
    color: SERIES[index],
    values: Object.fromEntries(points.filter((p) => (p.series ?? metricLabel) === name).map((p) => [p.x, p.value])),
  }));
  const top = niceTicks(Math.max(...points.map((p) => p.value), 0));
  const yMax = top[top.length - 1] || 1;
  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const x = (key) => PAD.left + (xs.length > 1 ? (xs.indexOf(key) / (xs.length - 1)) * plotW : plotW / 2);
  const y = (value) => PAD.top + plotH - (value / yMax) * plotH;
  const every = Math.ceil(xs.length / 6); // at most about six period labels on the axis

  function track(event) {
    const box = svgRef.current.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * WIDTH;
    const nearest = xs.reduce((best, key) => (Math.abs(x(key) - px) < Math.abs(x(best) - px) ? key : best), xs[0]);
    setHover(nearest);
  }

  return (
    <figure className="space-y-2">
      <figcaption className="text-sm text-muted">
        {metricLabel} by {dimensionLabel(timeDimension)}
        {seriesDimension ? ` and ${dimensionLabel(seriesDimension)}` : ""}
      </figcaption>
      {lines.length > 1 && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm" aria-label="Legend">
          {lines.map((line) => (
            <li key={line.name} className="flex items-center gap-1.5">
              <span aria-hidden className={`inline-block h-0.5 w-4 rounded ${line.color.key}`} />
              {line.name}
            </li>
          ))}
        </ul>
      )}
      <div className="relative">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="w-full touch-none"
          role="img"
          aria-label={`${metricLabel} over time`}
          onPointerMove={track}
          onPointerLeave={() => setHover(null)}
        >
          {top.map((tick) => (
            <g key={tick}>
              <line x1={PAD.left} x2={PAD.left + plotW} y1={y(tick)} y2={y(tick)} className="stroke-chart-grid" strokeWidth="1" />
              <text x={PAD.left - 8} y={y(tick) + 4} textAnchor="end" className="fill-chart-muted text-[11px] tabular-nums">
                {compact(tick)}
              </text>
            </g>
          ))}
          {xs.map((key, index) =>
            index % every === 0 ? (
              <text key={key} x={x(key)} y={HEIGHT - 8} textAnchor="middle" className="fill-chart-muted text-[11px]">
                {formatCell(timeDimension, key)}
              </text>
            ) : null,
          )}
          {hover && <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + plotH} className="stroke-chart-axis" strokeWidth="1" />}
          {lines.map((line) => {
            const keys = xs.filter((key) => key in line.values);
            const d = keys.map((key, i) => `${i ? "L" : "M"}${x(key)},${y(line.values[key])}`).join(" ");
            const last = keys[keys.length - 1];
            return (
              <g key={line.name}>
                <path d={d} fill="none" className={line.color.stroke} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
                {last && (
                  <>
                    <circle cx={x(last)} cy={y(line.values[last])} r="4" className={`${line.color.fill} stroke-surface`} strokeWidth="2" />
                    {lines.length > 1 && (
                      <text x={x(last) + 8} y={y(line.values[last]) + 4} className="fill-ink-2 text-[11px]">
                        {line.name}
                      </text>
                    )}
                  </>
                )}
                {hover && hover in line.values && (
                  <circle cx={x(hover)} cy={y(line.values[hover])} r="4" className={`${line.color.fill} stroke-surface`} strokeWidth="2" />
                )}
              </g>
            );
          })}
        </svg>
        {hover && (
          <div
            className="pointer-events-none absolute top-2 rounded-md border border-rule bg-surface px-2.5 py-1.5 text-sm shadow-sm"
            style={x(hover) > WIDTH / 2 ? { right: `${((WIDTH - x(hover)) / WIDTH) * 100 + 2}%` } : { left: `${(x(hover) / WIDTH) * 100 + 2}%` }}
            role="status"
          >
            <p className="mb-1 text-muted">{formatCell(timeDimension, hover)}</p>
            {lines
              .filter((line) => hover in line.values)
              .map((line) => (
                <p key={line.name} className="flex items-center gap-2">
                  <span aria-hidden className={`inline-block h-0.5 w-3 rounded ${line.color.key}`} />
                  <strong className="tabular-nums">{formatNumber(line.values[hover])}</strong>
                  {lines.length > 1 && <span className="text-muted">{line.name}</span>}
                </p>
              ))}
          </div>
        )}
      </div>
    </figure>
  );
}
