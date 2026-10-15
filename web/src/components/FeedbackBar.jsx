import { useEffect, useState } from "react";

import { postJSON } from "../lib/api";

/** Was this answer right? A thumbs up or down, then an optional comment, sent to the console's admins. */
export default function FeedbackBar({ traceId, question }) {
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState("");
  const [state, setState] = useState("idle"); // idle | saving | saved | failed
  useEffect(() => {
    setRating(0);
    setComment("");
    setState("idle");
  }, [traceId]);
  if (!traceId) return null;

  async function save(nextRating, nextComment = comment) {
    setRating(nextRating);
    setState("saving");
    try {
      await postJSON("/api/feedback", { trace_id: traceId, question, rating: nextRating, comment: nextComment });
      setState("saved");
    } catch {
      setState("failed");
    }
  }
  const button = (value, label, symbol) => (
    <button
      type="button"
      onClick={() => save(value)}
      aria-pressed={rating === value}
      className={`rounded-md border px-2.5 py-1 text-sm transition-colors ${
        rating === value ? (value > 0 ? "border-gain bg-code-tint text-gain" : "border-loss bg-fail-tint text-loss") : "border-rule hover:border-ink"
      }`}
    >
      <span aria-hidden>{symbol}</span> {label}
    </button>
  );
  return (
    <div className="space-y-2 rounded-xl border border-dashed border-rule p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted">Was this answer right?</span>
        {button(1, "Helpful", "▲")}
        {button(-1, "Not right", "▼")}
        {state === "saved" && <span className="text-gain">Thank you, noted.</span>}
        {state === "failed" && <span className="text-loss">Could not save; try again.</span>}
      </div>
      {rating !== 0 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save(rating, comment);
          }}
          className="flex flex-col gap-2 sm:flex-row"
        >
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            maxLength={2000}
            placeholder={rating > 0 ? "What was useful? (optional)" : "What was wrong or missing? (optional)"}
            aria-label="Comment on this answer"
            className="flex-1 rounded-lg border border-rule bg-surface px-3 py-1.5 text-sm"
          />
          <button type="submit" disabled={!comment.trim() || state === "saving"} className="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-40">
            Send comment
          </button>
        </form>
      )}
    </div>
  );
}
