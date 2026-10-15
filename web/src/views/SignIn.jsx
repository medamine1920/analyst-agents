import { useState } from "react";

import { postJSON } from "../lib/api";

/** An invite code from the address (#signup?code=BETA-...), so a shared link opens the sign-up form ready. */
function codeFromLink() {
  const found = window.location.hash.match(/signup\?code=([A-Za-z0-9-]+)/);
  return found ? found[1] : "";
}

/** A tester creates their own account with an invite code, then is signed straight in. */
function SignUp({ onSignedIn, onBack }) {
  const [form, setForm] = useState({ code: codeFromLink(), name: "", password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await postJSON("/api/auth/signup", { code: form.code.trim(), name: form.name.trim(), password: form.password });
      window.location.hash = "#ask";
      onSignedIn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const field = "w-full rounded-lg border border-rule bg-surface px-3 py-2";
  return (
    <form onSubmit={submit} className="mt-8 space-y-4 rounded-xl border border-rule bg-surface p-5">
      <label className="block space-y-1.5">
        <span className="block text-sm font-medium">Invite code</span>
        <input value={form.code} onChange={set("code")} placeholder="BETA-XXXXXXXX" autoComplete="off" className={`${field} font-mono uppercase`} />
      </label>
      <label className="block space-y-1.5">
        <span className="block text-sm font-medium">Choose a name</span>
        <input value={form.name} onChange={set("name")} autoComplete="username" maxLength={40} className={field} />
        <span className="block text-xs text-muted">Letters, digits, dots, dashes or underscores. It is how you sign in.</span>
      </label>
      <label className="block space-y-1.5">
        <span className="block text-sm font-medium">Choose a password</span>
        <input type="password" value={form.password} onChange={set("password")} autoComplete="new-password" className={field} />
        <span className="block text-xs text-muted">At least 10 characters; a short sentence works well.</span>
      </label>
      {error && (
        <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy || !form.code.trim() || !form.name.trim() || form.password.length < 10}
        className="w-full rounded-lg bg-brand px-4 py-2 font-medium text-white hover:bg-brand-dark disabled:opacity-50"
      >
        {busy ? "Creating your account…" : "Create my account"}
      </button>
      <p className="text-xs text-muted">
        Your account and your data are private to you.{" "}
        <a href="/privacy.html" className="underline">How your data is handled</a> ·{" "}
        <button type="button" onClick={onBack} className="underline">
          I already have an account
        </button>
      </p>
    </form>
  );
}

/** Sign in, or create an account with an invite code. */
export default function SignIn({ onSignedIn }) {
  const [joining, setJoining] = useState(() => window.location.hash.startsWith("#signup"));
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
      <p className="mt-2 text-muted">
        Ask questions about your business data in plain words; every number is computed and checked before you see it.
      </p>
      {joining ? (
        <SignUp onSignedIn={onSignedIn} onBack={() => setJoining(false)} />
      ) : (
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
          className="w-full rounded-lg bg-brand hover:bg-brand-dark px-4 py-2 font-medium text-white disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="text-xs text-muted">
          Have an invite code?{" "}
          <button type="button" onClick={() => setJoining(true)} className="font-medium text-brand underline">
            Create your account
          </button>{" "}
          · <a href="/privacy.html" className="underline">Privacy</a>
        </p>
      </form>
      )}
    </div>
  );
}
