import { useCallback, useEffect, useState } from "react";

import { getJSON, postJSON } from "../lib/api";
import { linkTo } from "../lib/route";
import AccessKeys from "./AccessKeys";
import { formatWhen } from "../lib/format";

function Badge({ children }) {
  return <span className="rounded-full bg-paper px-2 py-0.5 text-xs text-muted">{children}</span>;
}

function Message({ message }) {
  if (!message) return null;
  return (
    <p role={message.ok ? "status" : "alert"} className={`rounded-md px-3 py-2 text-sm ${message.ok ? "bg-code-tint text-code" : "bg-fail-tint text-fail"}`}>
      {message.text}
      {message.source && (
        <>
          {" "}
          <a href={linkTo("sources")} className="underline">See it in Data sources</a>
        </>
      )}
    </p>
  );
}

/** The form for one kind: plain fields, and secret ones that are never shown again once saved. */
function ConnectForm({ kind, onDone }) {
  const [values, setValues] = useState({});
  const [name, setName] = useState(kind.label);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await postJSON("/api/connectors", { kind: kind.kind, name, values });
      onDone(`${kind.label} is connected.`);
    } catch (e) {
      setMessage({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function google() {
    setMessage(null);
    try {
      const { url } = await getJSON(`/api/connectors/google/start?name=${encodeURIComponent(name)}`);
      window.location.href = url; // Google asks the user, then comes back to this console
    } catch (e) {
      setMessage({ ok: false, text: e.message });
    }
  }

  if (kind.oauth) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted">You will sign in with Google and choose what this console may read. It can then list your Sheets and spreadsheet files, import them, and save results as new Sheets.</p>
        <button type="button" onClick={google} disabled={!kind.available} className="rounded-lg bg-brand hover:bg-brand-dark px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
          Sign in with Google
        </button>
        {!kind.available && <p className="text-sm text-muted">Not set up on this console yet: the administrator must add a Google OAuth app (see docs/connectors.md).</p>}
        <Message message={message} />
      </div>
    );
  }
  const input = "w-full rounded-lg border border-rule bg-surface px-3 py-2";
  return (
    <form onSubmit={submit} className="space-y-3">
      <label className="block space-y-1">
        <span className="block text-sm font-medium">Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} className={input} />
      </label>
      {kind.fields.map((f) => (
        <label key={f.key} className="block space-y-1">
          <span className="block text-sm font-medium">
            {f.label} {!f.required && <span className="font-normal text-muted">(optional)</span>}
          </span>
          <input
            type={f.secret ? "password" : "text"}
            autoComplete={f.secret ? "new-password" : "off"}
            value={values[f.key] ?? ""}
            onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
            placeholder={f.placeholder}
            className={input}
          />
          {f.help && <span className="block text-xs text-muted">{f.help}</span>}
          {f.secret && <span className="block text-xs text-muted">Stored encrypted. After saving, only a masked hint is ever shown.</span>}
        </label>
      ))}
      <Message message={message} />
      <button type="submit" disabled={busy} className="rounded-lg bg-brand hover:bg-brand-dark px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
        {busy ? "Checking…" : "Connect"}
      </button>
    </form>
  );
}

/** Choose what to import from a connection, then it becomes a data source. */
function ImportPanel({ connection, onDone }) {
  const [items, setItems] = useState(null);
  const [item, setItem] = useState("");
  const [args, setArgs] = useState("{}");
  const [name, setName] = useState("");
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    getJSON(`/api/connectors/${connection.id}/items`)
      .then((list) => {
        setItems(list ?? []);
        setItem(list?.[0]?.id ?? "");
      })
      .catch((e) => setMessage({ ok: false, text: e.message }));
  }, [connection.id]);
  const chosen = items?.find((i) => i.id === item);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      let argumentsValue = {};
      if (connection.kind === "mcp") argumentsValue = JSON.parse(args || "{}");
      const source = await postJSON(`/api/connectors/${connection.id}/import`, { item, name: name || chosen?.label || connection.name, arguments: argumentsValue });
      setMessage({ ok: true, text: `Importing ${source.tables_found.map((t) => `${t.name} (${t.rows} rows)`).join(", ")}: building its metrics now.`, source: true });
      onDone();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof SyntaxError ? "The tool arguments must be valid JSON, e.g. {}" : e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 border-t border-rule pt-3">
      {!items ? (
        <p className="text-sm text-muted">Looking at what {connection.name} offers…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted">Nothing to import was found.</p>
      ) : (
        <>
          <label className="block space-y-1">
            <span className="block text-sm font-medium">{connection.kind === "mcp" ? "Tool" : "What to import"}</span>
            <select value={item} onChange={(e) => setItem(e.target.value)} className="w-full rounded-lg border border-rule bg-surface px-3 py-2">
              {items.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.label}
                  {i.description ? ` · ${i.description.slice(0, 60)}` : ""}
                </option>
              ))}
            </select>
          </label>
          {connection.kind === "mcp" && (
            <label className="block space-y-1">
              <span className="block text-sm font-medium">Tool arguments (JSON)</span>
              <textarea value={args} onChange={(e) => setArgs(e.target.value)} rows={2} className="w-full rounded-lg border border-rule bg-surface px-3 py-2 font-mono text-xs" />
              {chosen?.arguments && Object.keys(chosen.arguments).length > 0 && (
                <span className="block text-xs text-muted">This tool takes: {Object.keys(chosen.arguments).join(", ")}</span>
              )}
            </label>
          )}
          <label className="block space-y-1">
            <span className="block text-sm font-medium">Name of the new data source</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={chosen?.label} maxLength={80} className="w-full rounded-lg border border-rule bg-surface px-3 py-2" />
          </label>
          <button type="submit" disabled={busy || !item} className="rounded-lg bg-brand hover:bg-brand-dark px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
            {busy ? "Importing…" : "Import as a data source"}
          </button>
        </>
      )}
      <Message message={message} />
    </form>
  );
}

function LinkImport() {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const source = await postJSON("/api/connectors/link/import", { url: url.trim(), name: name.trim() });
      setMessage({ ok: true, text: `Imported ${source.tables_found.map((t) => `${t.name} (${t.rows} rows)`).join(", ")}: building its metrics now.`, source: true });
      setUrl("");
    } catch (e) {
      setMessage({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl border border-rule bg-surface p-5">
      <h3 className="font-semibold">Import from a link</h3>
      <p className="text-sm text-muted">A CSV, Excel or JSON file at a public address, or a Google Sheet shared as “anyone with the link”. No account needed.</p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" aria-label="Address of the file" className="flex-1 rounded-lg border border-rule bg-surface px-3 py-2" />
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (optional)" aria-label="Name of the new data source" className="rounded-lg border border-rule bg-surface px-3 py-2 sm:w-48" />
        <button type="submit" disabled={busy || !url.trim()} className="rounded-lg bg-brand hover:bg-brand-dark px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
          {busy ? "Importing…" : "Import"}
        </button>
      </div>
      <Message message={message} />
    </form>
  );
}

/** Connections to the tools analysts use: bring data in, send results out. */
export default function Connections({ params = {} }) {
  const [catalog, setCatalog] = useState(null);
  const [connections, setConnections] = useState([]);
  const [adding, setAdding] = useState(null);
  const [importing, setImporting] = useState(null);
  const [message, setMessage] = useState(
    params.google === "connected" ? { ok: true, text: "Google is connected." } : params.google ? { ok: false, text: "Google sign-in did not finish. Try again." } : null,
  );
  const load = useCallback(() => getJSON("/api/connectors").then(setConnections).catch(() => setConnections([])), []);
  useEffect(() => {
    getJSON("/api/connectors/catalog").then(setCatalog).catch((e) => setMessage({ ok: false, text: e.message }));
    load();
  }, [load]);
  const kinds = Object.fromEntries((catalog?.kinds ?? []).map((k) => [k.kind, k]));

  async function remove(connection) {
    if (!window.confirm(`Remove ${connection.name}? Its stored credential is deleted.`)) return;
    await fetch(`/api/connectors/${connection.id}`, { method: "DELETE" });
    load();
  }

  return (
    <div className="space-y-8">
      <Message message={message} />
      <LinkImport />
      <section className="space-y-3" aria-label="Your connections">
        <h2 className="font-semibold">Your connections</h2>
        {connections.length === 0 ? (
          <p className="text-sm text-muted">None yet. Connect a tool below.</p>
        ) : (
          <ul className="space-y-3">
            {connections.map((c) => (
              <li key={c.id} className="space-y-3 rounded-xl border border-rule bg-surface p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="font-medium">{c.name}</p>
                    <p className="text-xs text-muted">
                      {kinds[c.kind]?.label ?? c.kind} · added {formatWhen(c.created_at, { time: false })} · last used {formatWhen(c.last_used_at, { time: false })}
                      {c.hint ? ` · ${c.hint}` : ""}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    {kinds[c.kind]?.imports && (
                      <button type="button" onClick={() => setImporting(importing === c.id ? null : c.id)} className="rounded-md border border-rule px-3 py-1 text-sm hover:border-ink">
                        Import…
                      </button>
                    )}
                    <button type="button" onClick={() => remove(c)} className="rounded-md border border-rule px-3 py-1 text-sm hover:border-fail hover:text-fail">
                      Remove
                    </button>
                  </div>
                </div>
                {kinds[c.kind]?.exports && <p className="text-xs text-muted">Answers can be sent here from the Export menu under each answer.</p>}
                {importing === c.id && <ImportPanel connection={c} onDone={load} />}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="space-y-3" aria-label="Connect a tool">
        <h2 className="font-semibold">Connect a tool</h2>
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(catalog?.kinds ?? []).filter((k) => k.kind !== "link").map((k) => (
            <li key={k.kind} className="space-y-3 rounded-xl border border-rule bg-surface p-4 transition-shadow hover:shadow-sm">
              <div className="space-y-1">
                <p className="font-medium">{k.label}</p>
                <p className="text-sm text-muted">{k.description}</p>
                <p className="flex gap-1.5 pt-1">
                  {k.imports && <Badge>brings data in</Badge>}
                  {k.exports && <Badge>receives results</Badge>}
                </p>
              </div>
              {adding === k.kind ? (
                <ConnectForm kind={k} onDone={(text) => (setAdding(null), setMessage({ ok: true, text }), load())} />
              ) : (
                <button type="button" onClick={() => setAdding(k.kind)} className="rounded-md border border-rule px-3 py-1.5 text-sm hover:border-ink">
                  Connect
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>
      <section className="space-y-3" aria-label="Access for other apps">
        <h2 className="font-semibold">Access for other apps</h2>
        <p className="text-sm text-muted">
          Let a script, a spreadsheet or an MCP client (Claude Desktop, Cursor) ask the analyst for you, with a key made
          here. To use your own Claude, GPT or Gemini key, go to <a href={linkTo("settings", { tab: "keys" })} className="underline">Settings → API keys</a>.
        </p>
        <AccessKeys />
      </section>
    </div>
  );
}
