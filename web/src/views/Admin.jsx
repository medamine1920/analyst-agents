import { useEffect, useState } from "react";

import { getJSON, postJSON } from "../lib/api";
import { formatNumber, formatWhen } from "../lib/format";

async function send(path, method) {
  const response = await fetch(path, { method });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ?? `The request failed (${response.status}).`);
  return body;
}

/** A one-time password, shown once, to pass on to its owner. */
function OneTimePassword({ shown, onDone }) {
  return (
    <div role="status" className="space-y-2 rounded-xl border border-code bg-code-tint p-4 text-sm">
      <p className="font-medium text-code">
        One-time password for {shown.name}. Pass it on privately: it will not be shown again, and {shown.name} must choose a
        new password at first sign-in.
      </p>
      <p className="rounded-md border border-rule bg-surface px-3 py-2 font-mono text-base select-all">{shown.temporary_password}</p>
      <button type="button" onClick={onDone} className="underline">
        Done
      </button>
    </div>
  );
}

/** Invite people, reset passwords, change roles, remove accounts. Admins only. */
export default function Admin({ session }) {
  const [accounts, setAccounts] = useState(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState("user");
  const [shown, setShown] = useState(null);
  const [error, setError] = useState("");
  const load = () => getJSON("/api/admin/users").then(setAccounts).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  async function act(task) {
    setError("");
    try {
      await task();
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  const invite = (event) => {
    event.preventDefault();
    act(async () => {
      setShown(await postJSON("/api/admin/users", { name: name.trim(), role }));
      setName("");
    });
  };

  return (
    <div className="space-y-5">
      <form onSubmit={invite} className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="flex-1 space-y-1.5">
          <span className="block text-sm font-medium">Invite someone</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Their sign-in name, e.g. sara.k"
            maxLength={40}
            className="w-full rounded-lg border border-rule bg-surface px-3 py-2"
          />
        </label>
        <label className="space-y-1.5">
          <span className="block text-sm font-medium">Role</span>
          <select value={role} onChange={(e) => setRole(e.target.value)} className="rounded-lg border border-rule bg-surface px-3 py-2">
            <option value="user">User</option>
            <option value="admin">Admin</option>
          </select>
        </label>
        <button type="submit" disabled={!name.trim()} className="rounded-lg bg-ink px-4 py-2 font-medium text-white disabled:opacity-50">
          Create account
        </button>
      </form>
      {shown && <OneTimePassword shown={shown} onDone={() => setShown(null)} />}
      {error && (
        <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">
          {error}
        </p>
      )}
      {!accounts ? (
        <p className="text-muted">Loading accounts…</p>
      ) : (
        <div className="relative overflow-x-auto rounded-xl border border-rule bg-surface">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="text-left text-muted">
                <th className="px-3 py-2 font-medium">Name</th>
                <th className="px-3 py-2 font-medium">Role</th>
                <th className="px-3 py-2 font-medium">Last question</th>
                <th className="px-3 py-2 text-right font-medium">Tokens today</th>
                <th className="px-3 py-2 font-medium"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => {
                const self = account.name === session?.user;
                return (
                  <tr key={account.name} className="border-t border-rule">
                    <td className="px-3 py-2">
                      {account.name}
                      {self && <span className="text-muted"> (you)</span>}
                      {account.must_change && <span className="ml-2 text-xs text-partial">has not chosen a password yet</span>}
                    </td>
                    <td className="px-3 py-2">
                      <select
                        aria-label={`Role of ${account.name}`}
                        value={account.role}
                        onChange={(e) => act(() => postJSON(`/api/admin/users/${encodeURIComponent(account.name)}/role`, { role: e.target.value }))}
                        className="rounded-md border border-rule bg-surface px-2 py-1"
                      >
                        <option value="user">User</option>
                        <option value="admin">Admin</option>
                      </select>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap text-muted">{formatWhen(account.last_asked)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatNumber(account.tokens_today)}</td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => act(async () => setShown(await postJSON(`/api/admin/users/${encodeURIComponent(account.name)}/reset`)))}
                        className="mr-2 rounded-md border border-rule px-2.5 py-1 hover:border-ink"
                      >
                        Reset password
                      </button>
                      {!self && (
                        <button
                          type="button"
                          onClick={() => window.confirm(`Remove ${account.name}? Their keys stop working at once.`) && act(() => send(`/api/admin/users/${encodeURIComponent(account.name)}`, "DELETE"))}
                          className="rounded-md border border-rule px-2.5 py-1 hover:border-fail hover:text-fail"
                        >
                          Remove
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
