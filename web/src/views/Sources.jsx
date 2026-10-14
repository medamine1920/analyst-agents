import { useCallback, useEffect, useRef, useState } from "react";

import { getJSON, postJSON } from "../lib/api";
import { formatNumber } from "../lib/format";
import { linkTo } from "../lib/route";
import { chooseSource } from "../lib/source";

const KINDS = { sample: "Sample data", csv: "CSV files", file: "Database file", postgres: "PostgreSQL", mysql: "MySQL" };

async function upload(path, form) {
  const response = await fetch(path, { method: "POST", body: form });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `The upload failed (${response.status}).`);
  return body;
}

function Status({ source }) {
  if (source.status === "ready") return <span className="text-sm text-code">✓ Ready</span>;
  if (source.status === "failed") return <span className="text-sm text-fail">! Failed</span>;
  return (
    <span className="flex items-center gap-1.5 text-sm text-partial" role="status">
      <span aria-hidden className="h-2 w-2 animate-pulse rounded-full bg-partial" /> Building: reading the tables and
      making the metrics
    </span>
  );
}

function SourceCard({ source, onChanged }) {
  const [busy, setBusy] = useState(false);
  async function act(path, method) {
    setBusy(true);
    try {
      await fetch(path, { method });
    } finally {
      setBusy(false);
      onChanged();
    }
  }
  const ask = () => {
    chooseSource(source.id);
    window.location.hash = linkTo("ask", { mode: "live" });
  };
  return (
    <li className="space-y-2 rounded-xl border border-rule bg-surface p-4 transition-shadow hover:shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium">{source.name}</p>
          <p className="text-xs text-muted">{KINDS[source.kind] ?? source.kind}</p>
        </div>
        <Status source={source} />
      </div>
      {source.description && <p className="text-sm text-muted">{source.description}</p>}
      {source.status === "failed" && <p className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{source.error}</p>}
      {source.tables && (
        <p className="text-sm">
          {source.tables.map((t) => `${t.label}${t.rows != null ? ` (${formatNumber(t.rows)} rows)` : ""}`).join(", ")} ·{" "}
          {source.metrics} metrics
        </p>
      )}
      {source.notes?.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted">
          {source.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2 pt-1">
        <button type="button" onClick={ask} disabled={source.status !== "ready"} className="rounded-md bg-ink px-3 py-1 text-sm text-white disabled:opacity-40">
          Ask about it
        </button>
        {source.kind !== "sample" && (
          <>
            <button type="button" disabled={busy || source.status === "building"} onClick={() => act(`/api/sources/${source.id}/rebuild`, "POST")} className="rounded-md border border-rule px-3 py-1 text-sm hover:border-ink disabled:opacity-40">
              Read again
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => window.confirm(`Remove ${source.name}? Its files and metrics are deleted.`) && act(`/api/sources/${source.id}`, "DELETE")}
              className="rounded-md border border-rule px-3 py-1 text-sm hover:border-fail hover:text-fail"
            >
              Remove
            </button>
          </>
        )}
      </div>
    </li>
  );
}

function FilesForm({ kind, onAdded }) {
  const [name, setName] = useState("");
  const [files, setFiles] = useState([]);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef(null);
  const csv = kind === "csv";

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData();
    form.append("name", name.trim() || files[0]?.name || "My data");
    files.forEach((file) => form.append(csv ? "files" : "file", file));
    try {
      await upload(csv ? "/api/sources/csv" : "/api/sources/file", form);
      setName("");
      setFiles([]);
      onAdded();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <label className="block space-y-1.5">
        <span className="block text-sm font-medium">Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={csv ? "e.g. Sales 2025" : "e.g. Shop database"} className="w-full rounded-lg border border-rule bg-surface px-3 py-2" />
      </label>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          setFiles(csv ? [...e.dataTransfer.files] : [...e.dataTransfer.files].slice(0, 1));
        }}
        className={`rounded-xl border-2 border-dashed p-6 text-center text-sm transition-colors ${dragging ? "border-ink bg-paper" : "border-rule"}`}
      >
        <p>{csv ? "Drop CSV files here, one table each" : "Drop a .sqlite, .db or .duckdb file here"}</p>
        <button type="button" onClick={() => input.current?.click()} className="mt-2 underline">
          or choose {csv ? "files" : "a file"}
        </button>
        <input
          ref={input}
          type="file"
          multiple={csv}
          accept={csv ? ".csv,.tsv,.txt" : ".sqlite,.sqlite3,.db,.duckdb"}
          onChange={(e) => setFiles([...e.target.files])}
          className="sr-only"
          aria-label={csv ? "CSV files" : "Database file"}
        />
        {files.length > 0 && <p className="mt-3 text-muted">{files.map((f) => f.name).join(", ")}</p>}
      </div>
      {error && <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>}
      <button type="submit" disabled={busy || !files.length} className="rounded-lg bg-ink px-4 py-2 font-medium text-white disabled:opacity-50">
        {busy ? "Uploading…" : "Add data source"}
      </button>
      <p className="text-xs text-muted">Up to 50 MB. Files stay on this server and only you can ask about them.</p>
    </form>
  );
}

function ConnectionForm({ onAdded }) {
  const [form, setForm] = useState({ name: "", kind: "postgres", host: "", port: "", database: "", user: "", password: "", schema_name: "", tables: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (key) => (event) => setForm((f) => ({ ...f, [key]: event.target.value }));
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await postJSON("/api/sources/connection", { ...form, port: form.port ? Number(form.port) : null, schema_name: form.schema_name || null, tables: form.tables || null, name: form.name || form.database });
      setForm((f) => ({ ...f, password: "" }));
      onAdded();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const field = "w-full rounded-lg border border-rule bg-surface px-3 py-2";
  const row = (label, key, props = {}) => (
    <label className="block space-y-1.5">
      <span className="block text-sm font-medium">{label}</span>
      <input value={form[key]} onChange={set(key)} className={field} {...props} />
    </label>
  );
  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block space-y-1.5">
          <span className="block text-sm font-medium">Database</span>
          <select value={form.kind} onChange={set("kind")} className={field}>
            <option value="postgres">PostgreSQL</option>
            <option value="mysql">MySQL</option>
          </select>
        </label>
        {row("Name", "name", { placeholder: "e.g. Production orders" })}
        {row("Address", "host", { placeholder: "db.example.com", required: true })}
        {row("Port", "port", { placeholder: form.kind === "postgres" ? "5432" : "3306", inputMode: "numeric" })}
        {row("Database name", "database", { required: true })}
        {row("Schema (optional)", "schema_name", { placeholder: form.kind === "postgres" ? "public" : "" })}
        {row("User", "user", { required: true, autoComplete: "off" })}
        {row("Password", "password", { type: "password", autoComplete: "new-password" })}
        {row("Tables (optional)", "tables", { placeholder: "orders, customers — all tables if empty" })}
      </div>
      {error && <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>}
      <button type="submit" disabled={busy || !form.host || !form.database || !form.user} className="rounded-lg bg-ink px-4 py-2 font-medium text-white disabled:opacity-50">
        {busy ? "Connecting…" : "Connect"}
      </button>
      <p className="text-xs text-muted">
        The database is read live and never written to. Use an account that can only read. The password is stored
        encrypted on this server. Addresses on private networks are refused.
      </p>
    </form>
  );
}

const ADD_TABS = [
  ["csv", "Upload CSV files"],
  ["file", "Upload a database file"],
  ["live", "Connect a live database"],
];

/** Your own data: add it, see what was made of it, ask about it. */
export default function Sources() {
  const [sources, setSources] = useState(null);
  const [tab, setTab] = useState("csv");
  const [error, setError] = useState("");
  const load = useCallback(() => getJSON("/api/sources").then(setSources).catch((e) => setError(e.message)), []);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    // While something is building, check every few seconds.
    if (!sources?.some((s) => s.status === "building")) return undefined;
    const timer = setInterval(load, 2500);
    return () => clearInterval(timer);
  }, [sources, load]);

  return (
    <div className="grid grid-cols-1 gap-8 xl:grid-cols-[minmax(0,6fr)_minmax(0,5fr)]">
      <section className="space-y-3" aria-label="Your data sources">
        <p className="text-sm text-muted">
          Each source gets its own metrics: row counts, totals and averages of number columns, distinct counts of ID
          columns, broken down by its text and date columns. Only you can see and ask about your sources.
        </p>
        {error && <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>}
        {!sources ? (
          <p className="text-muted">Loading your data sources…</p>
        ) : (
          <ul className="space-y-3">
            {sources.map((source) => (
              <SourceCard key={source.id} source={source} onChanged={load} />
            ))}
          </ul>
        )}
      </section>
      <section className="space-y-4 self-start rounded-xl border border-rule bg-surface p-5" aria-label="Add a data source">
        <h2 className="font-semibold">Add a data source</h2>
        <div role="tablist" aria-label="Kind of data source" className="flex flex-wrap gap-1 border-b border-rule">
          {ADD_TABS.map(([id, text]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`-mb-px border-b-2 px-3 py-1.5 text-sm font-medium transition-colors ${tab === id ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink"}`}
            >
              {text}
            </button>
          ))}
        </div>
        {tab === "live" ? <ConnectionForm onAdded={load} /> : <FilesForm key={tab} kind={tab} onAdded={load} />}
      </section>
    </div>
  );
}
