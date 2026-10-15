import { lazy, Suspense, useContext, useMemo } from "react";

import { formatCell, formatNumber } from "../../lib/format";
import { dimensionLabel } from "../../lib/results";
import { compact } from "../../lib/scale";
import { PolarityContext, signColors, useSigns } from "../../lib/signs";
import { THEME, baseOption } from "./theme";

const EChart = lazy(() => import("./EChart"));

/**
 * What moved a total from one period to the next: the starting total, each group's change (gains up, losses
 * down, largest first), and the final total. Gain and loss colours follow Settings > Display, and for a
 * measure where lower is better (costs, returns) a rise is drawn as a loss.
 */
export default function WaterfallChart({ rows, metric, metricLabel, dimension, beforeLabel, afterLabel }) {
  const signs = useSigns();
  const polarity = useContext(PolarityContext);
  const label = `What changed ${metricLabel} by ${dimensionLabel(dimension)}, ${beforeLabel} to ${afterLabel}`;
  const option = useMemo(() => {
    const colors = signColors();
    const better = signs.basis === "meaning" ? polarity[metric] !== false : true;
    const steps = [...rows].sort((a, b) => b.change - a.change);
    const before = rows.reduce((sum, r) => sum + r.before, 0);
    const after = rows.reduce((sum, r) => sum + r.after, 0);
    const names = [beforeLabel, ...steps.map((r) => formatCell(dimension, r[dimension])), afterLabel];
    let level = before;
    const base = [0];
    const bars = [{ value: before, itemStyle: { color: THEME.series[0] } }];
    steps.forEach((r) => {
      const good = better ? r.change >= 0 : r.change <= 0;
      base.push(r.change >= 0 ? level : level + r.change);
      bars.push({ value: Math.abs(r.change), change: r.change, itemStyle: { color: good ? colors.gain : colors.loss } });
      level += r.change;
    });
    base.push(0);
    bars.push({ value: after, itemStyle: { color: THEME.series[0] } });
    return {
      ...baseOption,
      grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
      tooltip: {
        ...baseOption.tooltip,
        trigger: "item",
        formatter: (p) => {
          const change = bars[p.dataIndex].change;
          const shown = change === undefined ? formatNumber(p.value) : `${change > 0 ? "+" : ""}${formatNumber(change)}`;
          return `<b>${shown}</b><br/>${p.name}`;
        },
      },
      xAxis: {
        type: "category",
        data: names,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: THEME.axis } },
        axisLabel: { color: THEME.ink, interval: 0, rotate: names.length > 6 ? 30 : 0, width: 110, overflow: "truncate" },
      },
      yAxis: {
        type: "value",
        splitNumber: 4,
        axisLabel: { color: THEME.muted, formatter: (v) => compact(v) },
        splitLine: { lineStyle: { color: THEME.grid } },
      },
      series: [
        { type: "bar", stack: "fall", data: base, itemStyle: { color: "transparent" }, emphasis: { disabled: true }, tooltip: { show: false } },
        {
          type: "bar",
          stack: "fall",
          name: metricLabel,
          data: bars,
          barMaxWidth: 40,
          itemStyle: { borderRadius: 3 },
          label: {
            show: true,
            position: "top",
            color: THEME.ink2,
            formatter: (p) => {
              const change = bars[p.dataIndex].change;
              return change === undefined ? compact(p.value) : `${change > 0 ? "+" : "−"}${compact(Math.abs(change))}`;
            },
          },
        },
      ],
    };
  }, [rows, metric, metricLabel, dimension, beforeLabel, afterLabel, signs, polarity]);

  return (
    <figure className="space-y-1">
      <figcaption className="text-sm text-muted">{label}</figcaption>
      <Suspense fallback={<div style={{ height: 120 }} />}>
        <EChart option={option} height={320} label={label} />
      </Suspense>
      <ul className="sr-only">
        {rows.map((r) => (
          <li key={r[dimension]}>
            {formatCell(dimension, r[dimension])}: {formatNumber(r.before)} to {formatNumber(r.after)} (change {formatNumber(r.change)})
          </li>
        ))}
      </ul>
    </figure>
  );
}
