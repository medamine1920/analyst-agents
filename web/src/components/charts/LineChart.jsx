import { lazy, Suspense, useMemo } from "react";

import { formatCell, formatNumber } from "../../lib/format";
import { dimensionLabel } from "../../lib/results";
import { compact } from "../../lib/scale";
import { THEME, baseOption } from "./theme";

// ECharts is large: it loads the first time a chart is shown, not with the page.
const EChart = lazy(() => import("./EChart"));

const ZOOM_FROM = 24; // past this many periods the reader can zoom and pan along the time axis

/**
 * A metric over time: one line, or one per group (at most four). Series keep their colour by name,
 * sorted alphabetically, so a store is always the same colour. Hovering shows every series at that period.
 * variant: "line", "area" (the total filled in) or "stacked" (groups stacked into their total).
 */
export default function LineChart({ points, metricLabel, timeDimension, seriesDimension, variant = "line" }) {
  const xs = useMemo(() => [...new Set(points.map((p) => p.x))].sort(), [points]);
  const names = useMemo(() => [...new Set(points.map((p) => p.series ?? metricLabel))].sort(), [points, metricLabel]);
  const label = `${metricLabel} by ${dimensionLabel(timeDimension)}${seriesDimension ? ` and ${dimensionLabel(seriesDimension)}` : ""}`;

  const option = useMemo(() => {
    const several = names.length > 1;
    const zoom = xs.length > ZOOM_FROM;
    return {
      ...baseOption,
      legend: several ? { top: 0, left: 0, icon: "roundRect", itemWidth: 14, itemHeight: 3, textStyle: { color: THEME.ink } } : undefined,
      grid: { left: 8, right: several ? 96 : 24, top: several ? 36 : 28, bottom: zoom ? 44 : 8, containLabel: true },
      tooltip: {
        ...baseOption.tooltip,
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: THEME.axis } },
        valueFormatter: (v) => formatNumber(v),
      },
      xAxis: {
        type: "category",
        data: xs.map((x) => formatCell(timeDimension, x)),
        boundaryGap: false,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: THEME.axis } },
        axisLabel: { color: THEME.muted, hideOverlap: true },
      },
      yAxis: {
        type: "value",
        splitNumber: 4,
        axisLabel: { color: THEME.muted, formatter: (v) => compact(v) },
        splitLine: { lineStyle: { color: THEME.grid } },
      },
      dataZoom: zoom ? [{ type: "inside" }, { type: "slider", height: 18, bottom: 8, borderColor: THEME.grid }] : [],
      series: names.map((name) => {
        const values = Object.fromEntries(points.filter((p) => (p.series ?? metricLabel) === name).map((p) => [p.x, p.value]));
        return {
          type: "line",
          name,
          data: xs.map((x) => values[x] ?? null),
          connectNulls: false,
          lineStyle: { width: 2 },
          symbol: "circle",
          symbolSize: 7,
          showSymbol: xs.length <= 12,
          itemStyle: { borderColor: THEME.surface, borderWidth: 2 },
          emphasis: { focus: "series" },
          endLabel: several ? { show: true, color: THEME.ink2, formatter: (p) => p.seriesName } : undefined,
          stack: variant === "stacked" ? "total" : undefined,
          areaStyle: variant === "stacked" ? { opacity: 0.55 } : variant === "area" ? { opacity: 0.22 } : several ? undefined : { opacity: 0.08 },
        };
      }),
    };
  }, [points, xs, names, metricLabel, timeDimension, variant]);

  return (
    <figure className="space-y-1">
      <figcaption className="text-sm text-muted">{label}</figcaption>
      <Suspense fallback={<div style={{ height: 120 }} />}>
        <EChart option={option} height={280} label={label} />
      </Suspense>
      <ul className="sr-only">
        {points.map((p) => (
          <li key={`${p.series}-${p.x}`}>
            {formatCell(timeDimension, p.x)}
            {p.series ? `, ${p.series}` : ""}: {formatNumber(p.value)}
          </li>
        ))}
      </ul>
    </figure>
  );
}
