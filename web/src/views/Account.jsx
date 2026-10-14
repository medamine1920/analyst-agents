import { useState } from "react";

import { postJSON } from "../lib/api";
import { linkTo } from "../lib/route";

/** Change your own password. Also shown on its own when an invited user signs in for the first time. */
export function PasswordForm({ forced = false, onChanged }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const mismatch = repeat && next !== repeat;
  const short = next && next.length < 10;

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await postJSON("/api/account/password", { current, new: next });
      setCurrent("");
      setNext("");
      setRepeat("");
      setMessage({ ok: true, text: "Password changed. You were signed out everywhere else." });
      onChanged?.();
    } catch (e) {
      setMessage({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  const field = "w-full rounded-lg border border-rule bg-surface px-3 py-2";
  return (
    <form onSubmit={submit} className="max-w-sm space-y-4 rounded-xl border border-rule bg-surface p-5">
      {forced && <p className="text-sm">Choose your own password before you start. The one you were given only works once.</p>}
      <div className="space-y-1.5">
        <label htmlFor="current" className="block text-sm font-medium">
          {forced ? "Password you were given" : "Current password"}
        </label>
        <input id="current" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} className={field} />
      </div>
      <div className="space-y-1.5">
        <label htmlFor="new" className="block text-sm font-medium">
          New password
        </label>
        <input id="new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} className={field} />
        <p className={`text-xs ${short ? "text-fail" : "text-muted"}`}>At least 10 characters. A short sentence is easy to remember.</p>
      </div>
      <div className="space-y-1.5">
        <label htmlFor="repeat" className="block text-sm font-medium">
          New password again
        </label>
        <input id="repeat" type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} className={field} />
        {mismatch && <p className="text-xs text-fail">The two new passwords are different.</p>}
      </div>
      {message && (
        <p role={message.ok ? "status" : "alert"} className={`rounded-md px-3 py-2 text-sm ${message.ok ? "bg-code-tint text-code" : "bg-fail-tint text-fail"}`}>
          {message.text}
        </p>
      )}
      <button
        type="submit"
        disabled={busy || !current || !next || short || next !== repeat}
        className="w-full rounded-lg bg-ink px-4 py-2 font-medium text-white disabled:opacity-50"
      >
        {busy ? "Saving…" : "Change password"}
      </button>
    </form>
  );
}

export default function Account({ session }) {
  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section className="space-y-3">
        <h2 className="font-semibold">Password</h2>
        <PasswordForm />
      </section>
      <section className="space-y-3 text-sm">
        <h2 className="font-semibold">Your account</h2>
        <dl className="space-y-1 rounded-xl border border-rule bg-surface p-5">
          <div className="flex justify-between gap-4">
            <dt className="text-muted">Name</dt>
            <dd>{session?.user}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-muted">Role</dt>
            <dd>{session?.role === "admin" ? "Admin: can invite and manage accounts" : "User"}</dd>
          </div>
        </dl>
        <p className="text-muted">
          Your <a className="underline" href={linkTo("connectors")}>API keys</a> and your{" "}
          <a className="underline" href={linkTo("history")}>question history</a> are private to you.
        </p>
      </section>
    </div>
  );
}
