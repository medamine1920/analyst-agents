import { lazy, Suspense, useMemo } from "react";

import { formatCell, formatNumber } from "../../lib/format";
import { dimensionLabel } from "../../lib/results";
import { compact } from "../../lib/scale";
import { THEME, baseOption } from "./theme";

const EChart = lazy(() => import("./EChart"));
const SHADES = ["#174f96", "#2a78d6", "#5a98e2", "#8fb9ec", "#c3daf6"]; // one hue: larger shares darker

/** Shares of a total: each group a rectangle sized by its value, labelled with its share. */
export default function TreemapChart({ points, metricLabel, dimension }) {
  const label = `${metricLabel} by ${dimensionLabel(dimension)}: shares of the total`;
  const total = points.reduce((sum, p) => sum + (p.value ?? 0), 0) || 1;
  const option = useMemo(() => {
    const ordered = [...points].filter((p) => p.value > 0).sort((a, b) => b.value - a.value);
    return {
      ...baseOption,
      tooltip: {
        ...baseOption.tooltip,
        formatter: (p) => `<b>${formatNumber(p.value)}</b> (${((100 * p.value) / total).toFixed(1)}%)<br/>${p.name}`,
      },
      series: [{
        type: "treemap",
        name: metricLabel,
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        top: 28,
        left: 0,
        right: 0,
        bottom: 0,
        itemStyle: { borderColor: THEME.surface, borderWidth: 2, gapWidth: 2 },
        label: {
          show: true,
          color: "#ffffff",
          fontFamily: THEME.font,
          formatter: (p) => `${p.name}\n${compact(p.value)} · ${((100 * p.value) / total).toFixed(0)}%`,
        },
        data: ordered.map((p, i) => ({
          name: formatCell(dimension, p.x),
          value: p.value,
          itemStyle: { color: SHADES[Math.min(i, SHADES.length - 1)] },
          label: i >= 3 ? { color: THEME.ink } : undefined, // dark text on the light shades
        })),
      }],
    };
  }, [points, metricLabel, dimension, total]);

  return (
    <figure className="space-y-1">
      <figcaption className="text-sm text-muted">{label}</figcaption>
      <Suspense fallback={<div style={{ height: 120 }} />}>
        <EChart option={option} height={320} label={label} />
      </Suspense>
      <ul className="sr-only">
        {points.map((p) => (
          <li key={p.x}>
            {formatCell(dimension, p.x)}: {formatNumber(p.value)} ({((100 * (p.value ?? 0)) / total).toFixed(1)}%)
          </li>
        ))}
      </ul>
    </figure>
  );
}
