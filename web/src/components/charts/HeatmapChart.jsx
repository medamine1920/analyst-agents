import { lazy, Suspense, useMemo } from "react";

import { formatCell, formatNumber } from "../../lib/format";
import { dimensionLabel } from "../../lib/results";
import { compact } from "../../lib/scale";
import { THEME, baseOption } from "./theme";

const EChart = lazy(() => import("./EChart"));
// One hue, light to dark: size reads as depth of colour, never as a category.
const SCALE = ["#eaf2fc", "#b9d3f5", "#7aaee8", "#2a78d6", "#174f96"];

/**
 * Many groups over time: one row per group, one column per period, darker for larger. Where more than four
 * lines would tangle, the pattern (which group grows, which month peaks) stays readable. Groups are ordered
 * largest total first.
 */
export default function HeatmapChart({ points, metricLabel, timeDimension, seriesDimension }) {
  const label = `${metricLabel} by ${dimensionLabel(timeDimension)} and ${dimensionLabel(seriesDimension)}`;
  const { xs, groups, cells, max } = useMemo(() => {
    const totals = {};
    points.forEach((p) => (totals[p.series] = (totals[p.series] ?? 0) + (p.value ?? 0)));
    const groups = Object.keys(totals).sort((a, b) => totals[a] - totals[b]); // largest at the top
    const xs = [...new Set(points.map((p) => p.x))].sort();
    const cells = points.map((p) => [xs.indexOf(p.x), groups.indexOf(p.series), p.value]);
    return { xs, groups, cells, max: Math.max(...points.map((p) => p.value ?? 0), 0) };
  }, [points]);

  const option = useMemo(
    () => ({
      ...baseOption,
      grid: { left: 8, right: 16, top: 40, bottom: 56, containLabel: true }, // room for the save-image icon
      tooltip: {
        ...baseOption.tooltip,
        formatter: (p) => `<b>${formatNumber(p.value[2])}</b><br/>${groups[p.value[1]]} · ${formatCell(timeDimension, xs[p.value[0]])}`,
      },
      xAxis: {
        type: "category",
        data: xs.map((x) => formatCell(timeDimension, x)),
        splitArea: { show: false },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: THEME.axis } },
        axisLabel: { color: THEME.muted, hideOverlap: true },
      },
      yAxis: {
        type: "category",
        data: groups,
        axisTick: { show: false },
        axisLine: { show: false },
        axisLabel: { color: THEME.ink, width: 140, overflow: "truncate" },
      },
      visualMap: {
        min: 0,
        max: max || 1,
        calculable: false,
        orient: "horizontal",
        left: "center",
        bottom: 4,
        itemHeight: 120,
        itemWidth: 10,
        text: [compact(max), "0"],
        textStyle: { color: THEME.muted },
        inRange: { color: SCALE },
      },
      series: [{ type: "heatmap", name: metricLabel, data: cells, itemStyle: { borderColor: THEME.surface, borderWidth: 2, borderRadius: 3 } }],
    }),
    [cells, groups, xs, max, metricLabel, timeDimension],
  );

  return (
    <figure className="space-y-1">
      <figcaption className="text-sm text-muted">{label}</figcaption>
      <Suspense fallback={<div style={{ height: 120 }} />}>
        <EChart option={option} height={Math.max(groups.length * 30 + 110, 180)} label={label} />
      </Suspense>
      <ul className="sr-only">
        {points.map((p) => (
          <li key={`${p.series}-${p.x}`}>
            {p.series}, {formatCell(timeDimension, p.x)}: {formatNumber(p.value)}
          </li>
        ))}
      </ul>
    </figure>
  );
}
