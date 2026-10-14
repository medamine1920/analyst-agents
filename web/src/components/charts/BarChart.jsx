import { lazy, Suspense, useMemo } from "react";

import { formatCell, formatNumber } from "../../lib/format";
import { dimensionLabel } from "../../lib/results";
import { compact } from "../../lib/scale";
import { THEME, baseOption } from "./theme";

// ECharts is large: it loads the first time a chart is shown, not with the page.
const EChart = lazy(() => import("./EChart"));

const ROW = 34;

/** Horizontal bars for one metric across the groups of one breakdown, largest at the top. */
export default function BarChart({ points, metricLabel, dimension }) {
  const label = `${metricLabel} by ${dimensionLabel(dimension)}`;
  const option = useMemo(() => {
    const ordered = [...points].reverse(); // ECharts draws the first category at the bottom
    return {
      ...baseOption,
      grid: { left: 8, right: 64, top: 28, bottom: 8, containLabel: true },
      tooltip: { ...baseOption.tooltip, trigger: "item", formatter: (p) => `<b>${formatNumber(p.value)}</b><br/>${p.name}` },
      xAxis: {
        type: "value",
        splitNumber: 4,
        axisLabel: { color: THEME.muted, formatter: (v) => compact(v) },
        splitLine: { lineStyle: { color: THEME.grid } },
      },
      yAxis: {
        type: "category",
        data: ordered.map((p) => formatCell(dimension, p.x)),
        axisTick: { show: false },
        axisLine: { lineStyle: { color: THEME.axis } },
        axisLabel: { color: THEME.ink, width: 140, overflow: "truncate" },
      },
      series: [{
        type: "bar",
        name: metricLabel,
        data: ordered.map((p) => p.value),
        barMaxWidth: 22,
        itemStyle: { color: THEME.series[0], borderRadius: [0, 4, 4, 0] }, // rounded at the data end only
        emphasis: { itemStyle: { opacity: 0.85 } },
        label: { show: true, position: "right", color: THEME.ink2, formatter: (p) => compact(p.value) },
      }],
    };
  }, [points, metricLabel, dimension]);

  return (
    <figure className="space-y-1">
      <figcaption className="text-sm text-muted">{label}</figcaption>
      <Suspense fallback={<div style={{ height: 120 }} />}>
        <EChart option={option} height={Math.max(points.length * ROW + 48, 120)} label={label} />
      </Suspense>
      <ul className="sr-only">
        {points.map((p) => (
          <li key={p.x} aria-label={`${formatCell(dimension, p.x)}: ${formatNumber(p.value)}`}>
            {formatCell(dimension, p.x)}: {formatNumber(p.value)}
          </li>
        ))}
      </ul>
    </figure>
  );
}
