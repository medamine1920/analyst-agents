import { useState } from "react";

import { postJSON } from "../lib/api";

/** Accounts are created by the operator; there is no sign-up. */
export default function SignIn({ onSignedIn }) {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await postJSON("/api/auth/login", { name: name.trim(), password });
      setPassword("");
      onSignedIn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto mt-16 max-w-sm px-5">
      <h1 className="font-display text-[1.75rem] leading-none font-bold tracking-tight">Analyst Console</h1>
      <p className="mt-2 text-muted">Sign in to ask questions about the governed business metrics.</p>
      <form onSubmit={submit} className="mt-8 space-y-4 rounded-xl border border-rule bg-surface p-5">
        <div className="space-y-1.5">
          <label htmlFor="name" className="block text-sm font-medium">
            Name
          </label>
          <input
            id="name"
            autoComplete="username"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-lg border border-rule bg-surface px-3 py-2"
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="password" className="block text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-rule bg-surface px-3 py-2"
          />
        </div>
        {error && (
          <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy || !name.trim() || !password}
          className="w-full rounded-lg bg-ink px-4 py-2 font-medium text-white disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="text-xs text-muted">No account? Ask the person who runs this console to create one.</p>
      </form>
    </div>
  );
}
