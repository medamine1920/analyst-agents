import { useState } from "react";

const KEY = "analyst.welcome.seen";

function seen() {
  try {
    return window.localStorage.getItem(KEY) === "1";
  } catch {
    return false; // storage blocked: show the guide, it can still be closed for this visit
  }
}

const STEPS = [
  ["Ask in plain words", "Type a business question, or try one of the examples below. The sample coffee shop is ready to use."],
  ["See how it was built", "Every answer shows its data, a chart, the steps the agent took and the exact query, in your database's language."],
  ["Bring your own data", "Upload a CSV or connect a database or Google Drive under Data sources; it gets its own metrics and dashboard."],
];

/** A first-visit welcome: what the console is, and three ways in. Closed once, it stays closed in this browser. */
export default function WelcomeGuide() {
  const [open, setOpen] = useState(() => !seen());
  if (!open) return null;
  function close() {
    try {
      window.localStorage.setItem(KEY, "1");
    } catch {
      // not remembered: it simply shows again next visit
    }
    setOpen(false);
  }
  return (
    <section aria-label="Welcome" className="space-y-4 rounded-xl border border-brand-tint bg-surface p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Welcome to Analyst Console</h2>
          <p className="text-sm text-muted">
            An AI data analyst whose numbers you can trust: the AI plans and writes, code computes every number and checks the
            answer before you see it.
          </p>
        </div>
        <button type="button" onClick={close} aria-label="Close the welcome guide" className="text-muted hover:text-ink">
          ✕
        </button>
      </div>
      <ol className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {STEPS.map(([title, text], index) => (
          <li key={title} className="space-y-1 rounded-lg bg-paper p-3 text-sm">
            <p className="font-medium">
              <span className="mr-1.5 inline-grid h-5 w-5 place-items-center rounded-full bg-brand text-xs text-white">{index + 1}</span>
              {title}
            </p>
            <p className="text-muted">{text}</p>
          </li>
        ))}
      </ol>
      <p className="text-sm text-muted">
        This is a beta: under each answer, tell us whether it was right. Your feedback shapes what comes next.{" "}
        <button type="button" onClick={close} className="font-medium text-brand hover:underline">
          Got it
        </button>
      </p>
    </section>
  );
}
