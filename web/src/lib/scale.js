/** Axis helpers shared by the charts: clean tick values and compact labels. */

export function niceTicks(max, count = 4) {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw);
  const ticks = [];
  for (let value = 0; value < max + step / 2; value += step) ticks.push(Number(value.toFixed(10)));
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

export function compact(value) {
  const abs = Math.abs(value);
  if (abs >= 1e6) return `${(value / 1e6).toFixed(abs >= 1e7 ? 0 : 1)}M`;
  if (abs >= 1e4) return `${(value / 1e3).toFixed(0)}K`;
  return value.toLocaleString("en-US", { maximumFractionDigits: abs < 10 ? 2 : 0 });
}
