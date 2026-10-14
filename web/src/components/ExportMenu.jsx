import { useEffect, useRef, useState } from "react";

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

/** Export an answer: an Excel workbook of every table, or straight to a connected tool. */
export default function ExportMenu({ question, steps, traceId }) {
  const [open, setOpen] = useState(false);
  const [targets, setTargets] = useState([]);
  const [message, setMessage] = useState(null);
  const box = useRef(null);
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

  async function act(task, done) {
    setMessage(null);
    try {
      const result = await task();
      setMessage({ ok: true, text: done ?? result?.message ?? "Done." });
    } catch (e) {
      setMessage({ ok: false, text: e.message });
    }
    setOpen(false);
  }

  function send(target) {
    let extra = {};
    if (target.kind === "mcp") {
      const tool = window.prompt("Which tool of this MCP server should receive the answer?");
      if (!tool) return;
      extra = { tool, argument: window.prompt("Name of the tool's text argument", "text") || "text" };
    }
    act(() => postJSON(`/api/connectors/${target.id}/send`, { trace_id: traceId, ...extra }));
  }

  return (
    <div ref={box} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="rounded-md border border-rule px-2.5 py-1 text-sm hover:border-ink">
        Export ▾
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
      {message && (
        <p role={message.ok ? "status" : "alert"} className={`absolute right-0 mt-1 w-72 rounded-md px-3 py-2 text-xs ${message.ok ? "bg-code-tint text-code" : "bg-fail-tint text-fail"}`}>
          {message.text}
        </p>
      )}
    </div>
  );
}
