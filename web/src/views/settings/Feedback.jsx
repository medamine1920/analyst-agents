import { useEffect, useState } from "react";

import { getJSON } from "../../lib/api";
import { formatWhen } from "../../lib/format";
import { linkTo } from "../../lib/route";

/** What testers said about answers, newest first. Admins only. */
export default function Feedback() {
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getJSON("/api/admin/feedback").then(setItems).catch((e) => setError(e.message));
  }, []);
  if (error) return <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>;
  if (!items) return <p className="text-muted">Loading feedback…</p>;
  if (!items.length) return <p className="text-muted">No feedback yet. It appears here as testers rate answers.</p>;
  const up = items.filter((i) => i.rating > 0).length;
  return (
    <div className="space-y-4">
      <p className="text-sm">
        <span className="font-semibold text-gain">{up} helpful</span> ·{" "}
        <span className="font-semibold text-loss">{items.length - up} not right</span> · {items.length} in all
      </p>
      <ul className="space-y-2">
        {items.map((f) => (
          <li key={`${f.user_name}-${f.trace_id}`} className="space-y-1 rounded-xl border border-rule bg-surface p-4 text-sm">
            <p className="flex flex-wrap items-baseline justify-between gap-2">
              <a href={linkTo("ask", { trace: f.trace_id })} className="font-medium hover:underline">
                {f.question || f.trace_id}
              </a>
              <span className={f.rating > 0 ? "text-gain" : "text-loss"}>{f.rating > 0 ? "▲ Helpful" : "▼ Not right"}</span>
            </p>
            {f.comment && <p className="text-ink">“{f.comment}”</p>}
            <p className="text-xs text-muted">
              {f.user_name} · {formatWhen(f.at)}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
