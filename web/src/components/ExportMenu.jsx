import { useCallback, useEffect, useRef, useState } from "react";

import { getJSON, postJSON } from "../lib/api";
import { datasets } from "../lib/results";

async function downloadExcel(question, steps) {
  const tables = datasets(steps).map((d) => ({ name: d.title, rows: d.rows }));
  const response = await fetch("/api/export/xlsx", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: question.slice(0, 60) || "Analyst results", tables }),
  });
  if (!response.ok) throw new Error("The workbook could not be made.");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = `${(question.slice(0, 60) || "results").replace(/[^a-z0-9 _-]+/gi, "-")}.xlsx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/**
 * How an export went, in the corner of the screen where it cannot be missed: a tick and a link to the result
 * when it worked, a cross and the reason when it did not. Success fades after a while; a failure stays.
 */
function Notice({ message, onClose }) {
  useEffect(() => {
    if (!message?.ok) return undefined;
    const timer = setTimeout(onClose, 10000);
    return () => clearTimeout(timer);
  }, [message, onClose]);
  if (!message) return null;
  return (
    <div
      role={message.ok ? "status" : "alert"}
      className={`fixed right-4 bottom-4 z-50 flex max-w-sm items-start gap-3 rounded-xl border p-4 text-sm shadow-lg ${
        message.ok ? "border-code bg-surface" : "border-fail bg-surface"
      }`}
    >
      <span aria-hidden className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-white ${message.ok ? "bg-gain" : "bg-loss"}`}>
        {message.ok ? "✓" : "✕"}
      </span>
      <div className="space-y-1">
        <p className="font-medium">{message.ok ? "Export done" : "Export failed"}</p>
        <p className="text-muted">{message.text}</p>
        {message.url && (
          <a href={message.url} target="_blank" rel="noreferrer" className="font-medium text-brand hover:underline">
            Open it
          </a>
        )}
      </div>
      <button type="button" onClick={onClose} aria-label="Close" className="ml-auto text-muted hover:text-ink">
        ✕
      </button>
    </div>
  );
}

/** Export an answer: an Excel workbook of every table, or straight to a connected tool. */
export default function ExportMenu({ question, steps, traceId }) {
  const [open, setOpen] = useState(false);
  const [targets, setTargets] = useState([]);
  const [message, setMessage] = useState(null);
  const [sending, setSending] = useState(null); // the target being sent to, while it runs
  const box = useRef(null);
  const close = useCallback(() => setMessage(null), []);
  useEffect(() => {
    if (!open) return undefined;
    Promise.all([getJSON("/api/connectors"), getJSON("/api/connectors/catalog")])
      .then(([connections, catalog]) => {
        const exporting = new Set((catalog?.kinds ?? []).filter((k) => k.exports).map((k) => k.kind));
        setTargets((connections ?? []).filter((c) => exporting.has(c.kind)));
      })
      .catch(() => setTargets([]));
    const close = (event) => !box.current?.contains(event.target) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  async function act(task, done, label) {
    setMessage(null);
    setOpen(false);
    setSending(label);
    try {
      const result = await task();
      const text = done ?? (result?.message ?? "Done.").replace(/:?\s*https:\/\/\S+/, ".");
      setMessage({ ok: true, text, url: result?.url ?? null });
    } catch (e) {
      setMessage({ ok: false, text: e.message || "The export did not go through. Try again." });
    } finally {
      setSending(null);
    }
  }

  function send(target) {
    let extra = {};
    if (target.kind === "mcp") {
      const tool = window.prompt("Which tool of this MCP server should receive the answer?");
      if (!tool) return;
      extra = { tool, argument: window.prompt("Name of the tool's text argument", "text") || "text" };
    }
    act(() => postJSON(`/api/connectors/${target.id}/send`, { trace_id: traceId, ...extra }), null, target.name);
  }

  return (
    <div ref={box} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} disabled={Boolean(sending)} className="rounded-md border border-rule px-2.5 py-1 text-sm hover:border-ink disabled:opacity-60">
        {sending ? `Sending to ${sending}…` : "Export ▾"}
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-10 mt-1 w-64 rounded-lg border border-rule bg-surface p-1 shadow-md">
          <button type="button" role="menuitem" onClick={() => act(() => downloadExcel(question, steps), "Excel workbook downloaded.")} className="block w-full rounded-md px-3 py-2 text-left text-sm hover:bg-paper">
            Excel workbook (every table)
          </button>
          {traceId && targets.map((t) => (
            <button key={t.id} type="button" role="menuitem" onClick={() => send(t)} className="block w-full rounded-md px-3 py-2 text-left text-sm hover:bg-paper">
              Send to {t.name}
            </button>
          ))}
          {traceId && targets.length === 0 && (
            <a href="#connections" className="block rounded-md px-3 py-2 text-sm text-muted hover:bg-paper">Connect Slack, Teams or Google Sheets…</a>
          )}
        </div>
      )}
      <Notice message={message} onClose={close} />
    </div>
  );
}
