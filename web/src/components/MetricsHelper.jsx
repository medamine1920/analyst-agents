import { useState } from "react";

const SHOWN = 14;

/** What the agent can measure in this source, as a guide while writing a question: click one to add it. */
export default function MetricsHelper({ metrics, onPick }) {
  const [filter, setFilter] = useState("");
  const [all, setAll] = useState(false);
  if (!metrics.length) return null;
  const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
  const matching = metrics.filter((m) => words.every((w) => `${m.label} ${m.description}`.toLowerCase().includes(w)));
  const shown = all || filter ? matching : matching.slice(0, SHOWN);
  return (
    <section aria-label="Metrics you can ask about" className="space-y-3 rounded-xl border border-rule bg-surface/80 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold">Metrics you can ask about</h2>
        <a href="#metrics" className="text-sm text-brand hover:underline">
          Every definition
        </a>
      </div>
      <p className="text-sm text-muted">Click one to add it to your question; hover for how it is computed.</p>
      {metrics.length > SHOWN && (
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Find a metric…"
          aria-label="Find a metric"
          className="w-full rounded-lg border border-rule bg-surface px-3 py-1.5 text-sm"
        />
      )}
      <ul className="flex flex-wrap gap-1.5">
        {shown.map((m) => (
          <li key={m.name}>
            <button
              type="button"
              title={m.description}
              onClick={() => onPick(m)}
              className="rounded-full border border-brand-tint bg-brand-tint px-2.5 py-1 text-left text-sm text-brand-dark transition-colors hover:border-brand"
            >
              {m.label}
            </button>
          </li>
        ))}
      </ul>
      {!filter && matching.length > SHOWN && (
        <button type="button" onClick={() => setAll(!all)} className="text-sm text-muted underline hover:text-ink">
          {all ? "Show fewer" : `Show all ${matching.length}`}
        </button>
      )}
      {filter && !matching.length && <p className="text-sm text-muted">No metric matches “{filter}”.</p>}
    </section>
  );
}
