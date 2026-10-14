import { formatNumber } from "../../lib/format";

/** A result with no breakdown is a few numbers, not a chart: one tile per metric. */
export default function StatTiles({ row, metrics, labels }) {
  return (
    <dl className="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-3">
      {metrics.map((metric) => (
        <div key={metric} className="rounded-lg border border-rule bg-surface px-4 py-3">
          <dt className="text-sm text-muted">{labels[metric] ?? metric}</dt>
          <dd className="mt-1 text-2xl font-semibold">{formatNumber(row[metric])}</dd>
        </div>
      ))}
    </dl>
  );
}
