import { useEffect, useState } from "react";

import { getJSON } from "../lib/api";

/**
 * While a question is typed: questions asked before (most asked first, marked when a saved answer
 * exists and will cost nothing) and metrics whose name or definition match.
 */
export default function Suggestions({ text, onPickQuestion, onPickMetric }) {
  const [found, setFound] = useState({ questions: [], metrics: [] });

  useEffect(() => {
    const timer = setTimeout(() => {
      getJSON(`/api/suggestions?q=${encodeURIComponent(text.trim())}`)
        .then((result) => setFound(result ?? { questions: [], metrics: [] }))
        .catch(() => setFound({ questions: [], metrics: [] })); // suggestions are a convenience, never an error
    }, 250);
    return () => clearTimeout(timer);
  }, [text]);

  const questions = (found.questions ?? []).filter((q) => q.question.trim() !== text.trim());
  const metrics = found.metrics ?? [];
  if (!questions.length && !metrics.length) return null;
  return (
    <div className="space-y-2 rounded-lg border border-rule bg-surface p-3 text-sm" aria-label="Suggestions">
      {questions.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium tracking-wide text-muted uppercase">Asked before</p>
          <ul className="space-y-0.5">
            {questions.map((q) => (
              <li key={q.question}>
                <button
                  type="button"
                  onClick={() => onPickQuestion(q.question)}
                  className="flex w-full flex-wrap items-baseline gap-x-2 rounded px-1.5 py-1 text-left hover:bg-paper"
                >
                  <span className="text-ink">{q.question}</span>
                  <span className="text-xs text-muted">
                    asked {q.times_asked} {q.times_asked === 1 ? "time" : "times"}
                  </span>
                  {q.saved_answer && (
                    <span className="rounded-full bg-code-tint px-2 py-0.5 text-xs text-code">saved answer, no tokens</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {metrics.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium tracking-wide text-muted uppercase">Matching metrics</p>
          <div className="flex flex-wrap gap-1.5">
            {metrics.map((m) => (
              <button
                key={m.name}
                type="button"
                title={m.description}
                onClick={() => onPickMetric(m)}
                className="rounded-full border border-rule px-2.5 py-0.5 hover:border-ink"
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
