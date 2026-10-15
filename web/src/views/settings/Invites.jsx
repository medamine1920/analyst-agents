import { useCallback, useEffect, useState } from "react";

import { getJSON, postJSON } from "../../lib/api";
import { formatWhen } from "../../lib/format";

function CopyField({ label, text }) {
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
    <div className="space-y-1">
      <p className="text-xs text-muted">{label}</p>
      <div className="flex gap-2">
        <p className="flex-1 truncate rounded-md border border-rule bg-surface px-3 py-1.5 font-mono text-sm select-all">{text}</p>
        <button type="button" onClick={copy} className="rounded-md border border-rule px-2.5 text-sm hover:border-ink">
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

/** Invite codes: testers create their own private accounts with one. Admins only. */
export default function Invites() {
  const [codes, setCodes] = useState([]);
  const [form, setForm] = useState({ label: "LinkedIn beta", max_uses: "30", days: "30" });
  const [made, setMade] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(() => getJSON("/api/admin/invites").then(setCodes).catch((e) => setError(e.message)), []);
  useEffect(() => {
    load();
  }, [load]);
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  async function create(event) {
    event.preventDefault();
    setError("");
    try {
      setMade(await postJSON("/api/admin/invites", {
        label: form.label, max_uses: Number(form.max_uses), days: form.days === "never" ? null : Number(form.days),
      }));
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  async function switchOff(prefix) {
    if (!window.confirm("Switch this code off? Accounts already made with it keep working.")) return;
    await fetch(`/api/admin/invites/${encodeURIComponent(prefix)}`, { method: "DELETE" });
    load();
  }

  const field = "rounded-lg border border-rule bg-surface px-3 py-2";
  const link = made ? `${window.location.origin}/#signup?code=${made.code}` : "";
  return (
    <section aria-label="Invite codes" className="space-y-3 rounded-xl border border-rule bg-surface p-5">
      <h3 className="font-semibold">Invite codes</h3>
      <p className="text-sm text-muted">
        Share a code (or its link) and testers create their own private account with it. Each code works for the number
        of people and days you choose, and you can switch it off at any time.
      </p>
      <form onSubmit={create} className="flex flex-wrap items-end gap-2">
        <label className="space-y-1">
          <span className="block text-xs text-muted">For</span>
          <input value={form.label} onChange={set("label")} maxLength={80} className={`${field} w-48`} />
        </label>
        <label className="space-y-1">
          <span className="block text-xs text-muted">People</span>
          <input type="number" min="1" max="1000" value={form.max_uses} onChange={set("max_uses")} className={`${field} w-24`} />
        </label>
        <label className="space-y-1">
          <span className="block text-xs text-muted">Works for</span>
          <select value={form.days} onChange={set("days")} className={field}>
            <option value="7">7 days</option>
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="never">No end date</option>
          </select>
        </label>
        <button type="submit" className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-dark">
          Create code
        </button>
      </form>
      {error && <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>}
      {made && (
        <div role="status" className="space-y-2 rounded-xl border border-code bg-code-tint p-4">
          <p className="text-sm font-medium text-code">Code created. Copy it now: only its first characters are kept on screen.</p>
          <CopyField label="Code" text={made.code} />
          <CopyField label="Sign-up link (opens the form with the code filled in)" text={link} />
          <button type="button" onClick={() => setMade(null)} className="text-sm underline">
            Done
          </button>
        </div>
      )}
      {codes.length > 0 && (
        <ul className="divide-y divide-rule text-sm">
          {codes.map((c) => {
            const expired = c.expires_at && c.expires_at * 1000 < Date.now();
            const state = !c.active ? "switched off" : expired ? "expired" : c.uses >= c.max_uses ? "used up" : "open";
            return (
              <li key={c.prefix} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <span className="font-mono">{c.prefix}…</span> · {c.label} · {c.uses} of {c.max_uses} used ·{" "}
                  {c.expires_at ? `until ${formatWhen(c.expires_at, { time: false })}` : "no end date"} ·{" "}
                  <span className={state === "open" ? "text-gain" : "text-muted"}>{state}</span>
                </span>
                {state === "open" && (
                  <button type="button" onClick={() => switchOff(c.prefix)} className="rounded-md border border-rule px-2.5 py-1 hover:border-fail hover:text-fail">
                    Switch off
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
