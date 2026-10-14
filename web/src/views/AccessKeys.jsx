import { useEffect, useState } from "react";

import { getJSON, postJSON } from "../lib/api";
import { formatWhen } from "../lib/format";

function CodeBlock({ title, text }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard refused: the text can still be selected by hand
    }
  }
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">{title}</p>
        <button type="button" onClick={copy} className="text-sm text-muted underline hover:text-ink">
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto rounded-lg border border-rule bg-paper p-3 font-mono text-xs leading-relaxed">{text}</pre>
    </div>
  );
}

/** A new key, shown this one time only. */
function NewKey({ created, onDone }) {
  return (
    <div role="status" className="space-y-2 rounded-xl border border-code bg-code-tint p-4">
      <p className="font-medium text-code">Key “{created.name}” created. Copy it now: it will not be shown again.</p>
      <CodeBlock title="API key" text={created.key} />
      <button type="button" onClick={onDone} className="text-sm underline">
        I have stored it
      </button>
    </div>
  );
}

function examples(origin, key) {
  const shown = key ?? "ak_your_key";
  return [
    {
      title: "From a terminal (curl)",
      text: `curl -X POST ${origin}/api/v1/ask \\\n  -H "Authorization: Bearer ${shown}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"question": "Which store had the higher income?"}'`,
    },
    {
      title: "From Python",
      text: `import requests\n\nresult = requests.post(\n    "${origin}/api/v1/ask",\n    headers={"Authorization": "Bearer ${shown}"},\n    json={"question": "Which store had the higher income?"},\n    timeout=120,\n).json()\nprint(result["answer"])          # the checked answer\nprint(result["data"][0]["rows"])  # the numbers behind it`,
    },
    {
      title: "From an MCP client (for example Claude Desktop or Cursor)",
      text: JSON.stringify(
        { mcpServers: { analyst: { url: `${origin}/mcp`, headers: { Authorization: `Bearer ${shown}` } } } },
        null,
        2,
      ),
    },
  ];
}

/** Access keys for other applications (they ask the analyst as you), and how to use them. */
export default function AccessKeys() {
  const [keys, setKeys] = useState(null);
  const [name, setName] = useState("");
  const [days, setDays] = useState("90");
  const [created, setCreated] = useState(null);
  const [error, setError] = useState("");
  const load = () => getJSON("/api/keys").then(setKeys).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  async function create(event) {
    event.preventDefault();
    setError("");
    try {
      setCreated(await postJSON("/api/keys", { name: name.trim(), days: days === "never" ? null : Number(days) }));
      setName("");
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function revoke(prefix) {
    setError("");
    const response = await fetch(`/api/keys/${encodeURIComponent(prefix)}`, { method: "DELETE" });
    if (!response.ok) setError("The key could not be revoked. Try again.");
    load();
  }

  return (
    <div className="grid grid-cols-1 gap-8 xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <section className="space-y-4">
        <p className="text-sm text-muted">
          Keys let other applications (scripts, spreadsheets, MCP clients) ask the analyst as you: they use the
          model you chose under Settings → API keys (and so the same budget), count towards your hourly limit, and their questions appear in your history. A key is
          shown once, stored only as a fingerprint, and cannot create or revoke keys. Give each application its own
          key, so you can revoke one without touching the others.
        </p>
        {created && <NewKey created={created} onDone={() => setCreated(null)} />}
        <form onSubmit={create} className="flex flex-col gap-2 sm:flex-row">
          <label htmlFor="key-name" className="sr-only">
            What the key is for
          </label>
          <input
            id="key-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="What it is for, e.g. Sales spreadsheet"
            maxLength={80}
            className="flex-1 rounded-lg border border-rule bg-surface px-3 py-2"
          />
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted">Works for</span>
            <select value={days} onChange={(e) => setDays(e.target.value)} aria-label="How long the key works" className="rounded-lg border border-rule bg-surface px-2 py-2">
              <option value="30">30 days</option>
              <option value="90">90 days</option>
              <option value="365">1 year</option>
              <option value="never">No end date</option>
            </select>
          </label>
          <button type="submit" disabled={!name.trim()} className="rounded-lg bg-ink px-4 py-2 font-medium text-white disabled:opacity-50">
            Create key
          </button>
        </form>
        {error && (
          <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">
            {error}
          </p>
        )}
        {keys === null ? (
          <p className="text-muted">Loading your keys…</p>
        ) : keys.length === 0 ? (
          <p className="rounded-xl border border-dashed border-rule p-6 text-center text-sm text-muted">
            No keys yet. Create one for each application, so you can revoke them one at a time.
          </p>
        ) : (
          <ul className="divide-y divide-rule rounded-xl border border-rule bg-surface" aria-label="Your API keys">
            {keys.map((key) => (
              <li key={key.prefix} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm">
                <div>
                  <p className="font-medium">{key.name}</p>
                  <p className="text-xs text-muted">
                    <span className="font-mono">{key.prefix}…</span> · created {formatWhen(key.created_at)} ·{" "}
                    {key.expires_at ? (key.expires_at * 1000 < Date.now() ? "expired" : `works until ${formatWhen(key.expires_at)}`) : "no end date"} · last used{" "}
                    {formatWhen(key.last_used_at)}
                  </p>
                </div>
                <button type="button" onClick={() => revoke(key.prefix)} className="rounded-md border border-rule px-2.5 py-1 hover:border-fail hover:text-fail">
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="space-y-4" aria-label="How to connect">
        <h2 className="font-semibold">How to connect</h2>
        <p className="text-sm text-muted">
          Questions sent this way are never paused to ask about a definition: an ambiguous term is answered with every
          definition. A repeated question returns the saved answer at no cost; send <code className="font-mono">"fresh": true</code> to run it again.
        </p>
        {examples(window.location.origin, created?.key).map((example) => (
          <CodeBlock key={example.title} {...example} />
        ))}
      </section>
    </div>
  );
}
