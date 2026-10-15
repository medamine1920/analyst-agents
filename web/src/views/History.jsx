import { useEffect, useState } from "react";

import { getJSON } from "../lib/api";
import { formatNumber, formatWhen } from "../lib/format";
import { linkTo } from "../lib/route";

// Status is always spelled out: never colour alone.
const STATUS = {
  answered: { text: "Answered", mark: "✓", style: "text-code" },
  reused: { text: "Saved answer", mark: "↺", style: "text-code" },
  waiting: { text: "Waiting for a choice", mark: "…", style: "text-partial" },
  running: { text: "Running", mark: "…", style: "text-muted" },
  stopped: { text: "Stopped: the page was closed", mark: "■", style: "text-muted" },
  failed: { text: "Failed", mark: "!", style: "text-fail" },
};

/** The signed-in user's recent questions; each finished one reopens with all its steps. */
export default function History() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getJSON("/api/history").then(setRows).catch((e) => setError(e.message));
  }, []);

  if (error) return <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-fail">{error}</p>;
  if (!rows) return <p className="text-muted">Loading your questions…</p>;
  if (!rows.length) {
    return (
      <div className="rounded-xl border border-dashed border-rule p-8 text-center">
        <p className="font-medium">No questions yet</p>
        <p className="mt-1 text-sm text-muted">Questions you ask live appear here, with what each one cost.</p>
        <a href={linkTo("ask", { mode: "live" })} className="mt-4 inline-block rounded-lg bg-brand hover:bg-brand-dark px-4 py-2 text-sm font-medium text-white">
          Ask a question
        </a>
      </div>
    );
  }
  const spent = rows.reduce((sum, r) => sum + (r.tokens || 0), 0);
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        Your last {rows.length} questions used {formatNumber(spent)} model tokens. Saved answers cost nothing.
      </p>
      <div className="relative overflow-x-auto rounded-xl border border-rule bg-surface">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="text-left text-muted">
              <th className="px-3 py-2 font-medium">Question</th>
              <th className="px-3 py-2 font-medium">Asked</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 text-right font-medium">Tokens</th>
              <th className="px-3 py-2 font-medium"><span className="sr-only">Open</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const status = STATUS[row.status] ?? STATUS.running;
              return (
                <tr key={`${row.asked_at}-${index}`} className="border-t border-rule align-top">
                  <td className="max-w-md px-3 py-2">{row.question}</td>
                  <td className="px-3 py-2 whitespace-nowrap text-muted">{formatWhen(row.asked_at)}</td>
                  <td className={`px-3 py-2 whitespace-nowrap ${status.style}`}>
                    <span aria-hidden className="mr-1">{status.mark}</span>
                    {status.text}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{formatNumber(row.tokens || 0)}</td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    {row.trace_id ? (
                      <a href={linkTo("ask", { trace: row.trace_id })} className="font-medium underline">
                        Open
                      </a>
                    ) : (
                      <a href={linkTo("ask", { mode: "live", q: row.question })} className="text-muted underline">
                        Ask again
                      </a>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
