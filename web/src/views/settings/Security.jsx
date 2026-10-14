import QRCode from "qrcode";
import { useEffect, useState } from "react";

import { getJSON, postJSON } from "../../lib/api";
import { PasswordForm } from "../Account";

function when(seconds) {
  return seconds ? new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "unknown";
}

/** "Firefox on Windows" from a user-agent string, roughly. */
function device(agent = "") {
  const browser = /Edg\//.test(agent) ? "Edge" : /Chrome\//.test(agent) ? "Chrome" : /Firefox\//.test(agent) ? "Firefox" : /Safari\//.test(agent) ? "Safari" : agent ? "A browser" : "Unknown device";
  const system = /Windows/.test(agent) ? "Windows" : /Mac OS/.test(agent) ? "macOS" : /Android/.test(agent) ? "Android" : /iPhone|iPad/.test(agent) ? "iOS" : /Linux/.test(agent) ? "Linux" : "";
  return system ? `${browser} on ${system}` : browser;
}

function Card({ title, children }) {
  return (
    <section className="space-y-3 rounded-xl border border-rule bg-surface p-5">
      <h3 className="font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function TwoStep({ session, onChanged }) {
  const [setup, setSetup] = useState(null);
  const [qr, setQr] = useState("");
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function start() {
    setError("");
    const started = await postJSON("/api/account/2fa/start").catch((e) => setError(e.message));
    if (!started) return;
    setSetup(started);
    setQr(await QRCode.toDataURL(started.uri, { margin: 1, width: 180 }));
  }
  async function confirm(event) {
    event.preventDefault();
    setError("");
    try {
      setCodes((await postJSON("/api/account/2fa/confirm", { code })).recovery_codes);
      setSetup(null);
      onChanged();
    } catch (e) {
      setError(e.message);
    }
  }
  async function stop(event) {
    event.preventDefault();
    setError("");
    try {
      await postJSON("/api/account/2fa/stop", { password });
      setPassword("");
      onChanged();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <Card title="Two-step sign-in">
      <p className="text-sm text-muted">
        After your password, also ask for a code from an authenticator app (Google Authenticator, Microsoft
        Authenticator, 1Password). Someone who learns your password still cannot sign in.
      </p>
      {codes && (
        <div role="status" className="space-y-2 rounded-lg border border-code bg-code-tint p-3 text-sm">
          <p className="font-medium text-code">Two-step sign-in is on. Keep these recovery codes somewhere safe: each works once if you lose your phone, and they will not be shown again.</p>
          <p className="grid grid-cols-2 gap-1 font-mono">{codes.map((c) => <span key={c}>{c}</span>)}</p>
          <button type="button" onClick={() => setCodes(null)} className="underline">I have stored them</button>
        </div>
      )}
      {session?.two_factor && !codes ? (
        <form onSubmit={stop} className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <p className="text-sm text-code sm:mr-auto">✓ On</p>
          <label className="space-y-1">
            <span className="block text-xs text-muted">Password, to turn it off</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" className="rounded-lg border border-rule bg-surface px-3 py-1.5" />
          </label>
          <button type="submit" disabled={!password} className="rounded-lg border border-rule px-3 py-1.5 text-sm hover:border-fail hover:text-fail disabled:opacity-40">
            Turn off
          </button>
        </form>
      ) : !setup && !codes ? (
        <button type="button" onClick={start} className="rounded-lg bg-ink px-4 py-2 text-sm font-medium text-white">
          Turn on two-step sign-in
        </button>
      ) : null}
      {setup && (
        <form onSubmit={confirm} className="flex flex-col gap-4 sm:flex-row">
          {qr && <img src={qr} alt="QR code to scan with your authenticator app" width={180} height={180} className="rounded-lg border border-rule" />}
          <div className="space-y-2 text-sm">
            <p>1. Scan the code with your authenticator app, or enter this key by hand:</p>
            <p className="rounded-md bg-paper px-2 py-1 font-mono text-xs break-all select-all">{setup.secret}</p>
            <label className="block space-y-1">
              <span className="block">2. Enter the 6-digit code it shows:</span>
              <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={8} className="w-32 rounded-lg border border-rule bg-surface px-3 py-1.5 font-mono" />
            </label>
            <button type="submit" disabled={code.trim().length < 6} className="rounded-lg bg-ink px-4 py-1.5 font-medium text-white disabled:opacity-40">
              Confirm
            </button>
          </div>
        </form>
      )}
      {error && <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>}
    </Card>
  );
}

function Sessions() {
  const [sessions, setSessions] = useState(null);
  const load = () => getJSON("/api/account/sessions").then(setSessions).catch(() => setSessions([]));
  useEffect(() => {
    load();
  }, []);
  const end = (id) => fetch(`/api/account/sessions/${id}`, { method: "DELETE" }).finally(load);
  const endOthers = () => postJSON("/api/account/sessions/end-others").finally(load);
  return (
    <Card title="Where you are signed in">
      {!sessions ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : (
        <ul className="divide-y divide-rule text-sm">
          {sessions.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>
                {device(s.user_agent)} {s.current && <span className="text-code">· this browser</span>}
                <span className="block text-xs text-muted">
                  {s.ip || "unknown address"} · last active {when(s.last_seen)}
                </span>
              </span>
              {!s.current && (
                <button type="button" onClick={() => end(s.id)} className="rounded-md border border-rule px-2.5 py-1 hover:border-fail hover:text-fail">
                  Sign out
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {sessions?.length > 1 && (
        <button type="button" onClick={endOthers} className="text-sm underline">
          Sign out everywhere else
        </button>
      )}
    </Card>
  );
}

function Activity() {
  const [events, setEvents] = useState(null);
  useEffect(() => {
    getJSON("/api/account/activity").then(setEvents).catch(() => setEvents([]));
  }, []);
  return (
    <Card title="Recent account activity">
      <p className="text-sm text-muted">Sign-ins, failed attempts and changes to your account in the last 90 days. Anything you do not recognise: change your password and turn on two-step sign-in.</p>
      {!events ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : events.length === 0 ? (
        <p className="text-sm text-muted">Nothing yet.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {events.map((e, i) => (
            <li key={`${e.at}-${i}`} className="flex flex-wrap justify-between gap-2">
              <span className={/failed|wrong/.test(e.kind) ? "text-fail" : ""}>{e.kind}</span>
              <span className="text-xs text-muted">
                {when(e.at)}
                {e.ip ? ` · ${e.ip}` : ""}
                {e.user_agent ? ` · ${device(e.user_agent)}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function Security({ session, onChanged }) {
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
      <div className="space-y-5">
        <Card title="Password">
          <PasswordForm onChanged={onChanged} />
        </Card>
        <TwoStep session={session} onChanged={onChanged} />
      </div>
      <div className="space-y-5">
        <Sessions />
        <Activity />
      </div>
    </div>
  );
}
