import { useCallback, useEffect, useState } from "react";

import { getJSON, postJSON } from "../../lib/api";
import { formatWhen } from "../../lib/format";

async function send(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : `The request failed (${response.status}).`);
  return result;
}

/** Which model answers your questions right now. */
function Current({ current }) {
  if (!current) return null;
  return (
    <div className={`rounded-xl border p-4 text-sm ${current.own ? "border-code bg-code-tint" : "border-rule bg-surface"}`}>
      <p className="font-medium">
        Your questions run on: {current.own ? `${current.label} · ${current.model}` : `the console's model (${current.model})`}
      </p>
      <p className="mt-1 text-muted">
        {current.own
          ? "Billed to your own key by the provider. These questions do not use the console's shared daily budget."
          : "Shared by everyone on this console, within a daily budget. Add your own key to use another model."}
      </p>
    </div>
  );
}

function KeyCard({ entry, onChanged, onError }) {
  const [model, setModel] = useState(entry.model);
  const [busy, setBusy] = useState(false);
  async function act(fn) {
    setBusy(true);
    onError("");
    try {
      await fn();
      onChanged();
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const use = (chosen) => act(() => send("PUT", "/api/models/active", { provider: entry.provider, model: chosen }));
  return (
    <li className="space-y-3 rounded-xl border border-rule bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium">
            {entry.label} {entry.active && <span className="ml-1 rounded-full bg-code-tint px-2 py-0.5 text-xs text-code">In use</span>}
          </p>
          <p className="font-mono text-xs text-muted">
            {entry.hint} · added {formatWhen(entry.created_at, { time: false })}
          </p>
        </div>
        <div className="flex gap-2">
          {!entry.active && (
            <button type="button" disabled={busy} onClick={() => use(model)} className="rounded-md bg-ink px-3 py-1 text-sm text-white disabled:opacity-40">
              Use this key
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() => window.confirm(`Remove your ${entry.label} key? It is deleted from the console.`) && act(() => send("DELETE", `/api/models/keys/${entry.provider}`))}
            className="rounded-md border border-rule px-3 py-1 text-sm hover:border-fail hover:text-fail"
          >
            Remove
          </button>
        </div>
      </div>
      {entry.models.length > 0 && (
        <label className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">Model</span>
          <select
            value={model}
            disabled={busy}
            onChange={(e) => {
              setModel(e.target.value);
              if (entry.active) use(e.target.value);
            }}
            className="rounded-md border border-rule bg-surface px-2 py-1"
          >
            {entry.models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          {!entry.active && model !== entry.model && <span className="text-xs text-muted">Chosen when you use this key</span>}
        </label>
      )}
    </li>
  );
}

function AddKey({ providers, onAdded }) {
  const [provider, setProvider] = useState("anthropic");
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const spec = providers.find((p) => p.id === provider);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await postJSON("/api/models/keys", { provider, key, model: model.trim() || null });
      setKey("");
      setModel("");
      onAdded();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const field = "w-full rounded-lg border border-rule bg-surface px-3 py-2";
  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl border border-rule bg-surface p-5">
      <h3 className="font-semibold">Add a key</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block space-y-1.5">
          <span className="block text-sm font-medium">Provider</span>
          <select value={provider} onChange={(e) => setProvider(e.target.value)} className={field}>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block space-y-1.5">
          <span className="block text-sm font-medium">Model (optional)</span>
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder={spec?.default} className={field} />
        </label>
        <label className="block space-y-1.5 sm:col-span-2">
          <span className="block text-sm font-medium">API key</span>
          <input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            required
            minLength={10}
            className={`${field} font-mono`}
          />
          {spec && <span className="block text-xs text-muted">Create one at {spec.help}.</span>}
        </label>
      </div>
      {error && <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>}
      <button type="submit" disabled={busy || key.trim().length < 10} className="rounded-lg bg-ink px-4 py-2 font-medium text-white disabled:opacity-50">
        {busy ? "Checking the key…" : "Check and save"}
      </button>
      <p className="text-xs text-muted">
        The key is checked with the provider, then stored encrypted on this server. It is never shown again (only its
        first and last characters) and is used only for your questions.
      </p>
    </form>
  );
}

/** Your own keys for Claude, GPT, Gemini or Groq: your questions then run on the model you choose. */
export default function ModelKeys({ onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    getJSON("/api/models")
      .then(setData)
      .catch((e) => setError(e.message));
    onChanged?.(); // the header and the Ask page show which model answers
  }, [onChanged]);
  useEffect(() => {
    getJSON("/api/models")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  if (!data) return <p className="text-muted">{error || "Loading your keys…"}</p>;
  return (
    <div className="max-w-2xl space-y-6">
      <Current current={data.current} />
      {error && <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>}
      {data.keys.length > 0 && (
        <section className="space-y-3" aria-label="Your model keys">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold">Your keys</h3>
            {data.current?.own && (
              <button type="button" onClick={() => send("PUT", "/api/models/active", { provider: null }).then(load, (e) => setError(e.message))} className="text-sm text-muted underline hover:text-ink">
                Use the console's model instead
              </button>
            )}
          </div>
          <ul className="space-y-3">
            {data.keys.map((entry) => (
              <KeyCard key={`${entry.provider}-${entry.updated_at}`} entry={entry} onChanged={load} onError={setError} />
            ))}
          </ul>
        </section>
      )}
      <AddKey providers={data.providers} onAdded={load} />
    </div>
  );
}
