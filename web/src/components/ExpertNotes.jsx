const DIRECTION = { true: "Higher is better", false: "Lower is better", null: "Neither up nor down is better" };

/** The sales expert's notes on an answer: what each measure means, which way is good, and what to ask next. */
export default function ExpertNotes({ advice, onAsk }) {
  const { definitions = [], follow_ups: followUps = [] } = advice;
  if (!definitions.length && !followUps.length) return null;
  return (
    <section aria-label="From the sales expert" className="space-y-3 rounded-xl border border-rule bg-surface p-4">
      <h3 className="font-semibold">From the sales expert</h3>
      {definitions.length > 0 && (
        <dl className="space-y-2 text-sm">
          {definitions.map((d) => (
            <div key={d.metric}>
              <dt className="font-medium">
                {d.label}
                <span className="ml-2 text-xs font-normal text-muted">{DIRECTION[d.higher_is_better]}</span>
              </dt>
              <dd className="text-muted">
                {d.definition}
                {d.advice ? ` ${d.advice}` : ""}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {followUps.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-sm text-muted">Worth asking next</p>
          <ul className="flex flex-wrap gap-2">
            {followUps.map((q) => (
              <li key={q}>
                <button
                  type="button"
                  onClick={() => onAsk?.(q)}
                  disabled={!onAsk}
                  className="rounded-full border border-rule px-3 py-1 text-left text-sm hover:border-ink disabled:opacity-60"
                >
                  {q}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
