import { useState } from "react";

import { postJSON } from "../lib/api";

const LANGUAGES = [
  ["duckdb", "DuckDB (as run)"],
  ["postgres", "PostgreSQL"],
  ["mysql", "MySQL"],
  ["tsql", "SQL Server (T-SQL)"],
  ["oracle", "Oracle SQL"],
  ["plsql", "Oracle PL/SQL block"],
  ["snowflake", "Snowflake"],
  ["bigquery", "BigQuery"],
  ["sqlite", "SQLite"],
  ["mongodb", "MongoDB aggregation"],
];

function Translation({ set, source }) {
  const [language, setLanguage] = useState("postgres");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function show(chosen) {
    setLanguage(chosen);
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(await postJSON("/api/translate", { language: chosen, source, sql: set.sql, step: set.step }));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(result.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard refused: the text can still be selected by hand
    }
  }

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">{set.title}</h3>
        <div className="flex items-center gap-2 text-sm">
          <label className="flex items-center gap-2">
            <span className="text-muted">Language</span>
            <select value={language} onChange={(e) => show(e.target.value)} className="rounded-md border border-rule bg-surface px-2 py-1">
              {LANGUAGES.map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {!result && !busy && (
            <button type="button" onClick={() => show(language)} className="rounded-md bg-ink px-3 py-1 text-white">
              Show
            </button>
          )}
          {result && (
            <button type="button" onClick={copy} className="rounded-md border border-rule px-2.5 py-1 hover:border-ink">
              {copied ? "Copied" : "Copy"}
            </button>
          )}
        </div>
      </div>
      {busy && <p className="text-sm text-muted">Translating…</p>}
      {error && <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>}
      {result && (
        <>
          <pre className="max-h-96 overflow-auto rounded-lg border border-rule bg-paper p-3 font-mono text-xs leading-relaxed">{result.text}</pre>
          {result.note && <p className="text-xs text-muted">{result.note}</p>}
        </>
      )}
    </section>
  );
}

/** Each query behind the answer, in the language the analyst works in. Text only: nothing is run. */
export default function QueryView({ sets, source }) {
  return (
    <div className="space-y-6">
      <p className="text-sm text-muted">
        The exact queries behind these numbers, rewritten for your database. Dates, weeks (starting Monday) and
        division by zero behave as they did here; anything that cannot be written exactly in a language is refused
        rather than approximated.
      </p>
      {sets.map((set) => (
        <Translation key={set.id} set={set} source={source} />
      ))}
    </div>
  );
}
