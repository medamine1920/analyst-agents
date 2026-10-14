import { useCallback, useEffect, useState } from "react";

import { formatNumber } from "./lib/format";
import { getJSON, postJSON } from "./lib/api";
import Ask from "./views/Ask";
import Evals from "./views/Evals";
import Metrics from "./views/Metrics";
import SignIn from "./views/SignIn";

const VIEWS = [
  { id: "ask", label: "Ask", component: Ask },
  { id: "evals", label: "Evaluations", component: Evals },
  { id: "metrics", label: "Metrics", component: Metrics },
];

function currentView() {
  const id = window.location.hash.replace("#", "");
  return VIEWS.find((v) => v.id === id) ?? VIEWS[0];
}

/** Who is signed in and how much of today's budget is left; null until the first answer arrives. */
function useSession() {
  const [session, setSession] = useState(null);
  const refresh = useCallback(
    () =>
      getJSON("/api/auth/me")
        .then(setSession)
        .catch(() => setSession({ user: null, auth: true })),
    [],
  );
  useEffect(() => {
    refresh();
    const onSignedOut = () => setSession((s) => ({ ...s, user: null }));
    window.addEventListener("analyst:signed-out", onSignedOut);
    window.addEventListener("analyst:question-finished", refresh);
    return () => {
      window.removeEventListener("analyst:signed-out", onSignedOut);
      window.removeEventListener("analyst:question-finished", refresh);
    };
  }, [refresh]);
  return [session, refresh];
}

function Account({ session, onSignOut }) {
  if (!session?.auth || !session.user) return null;
  const usage = session.usage;
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
      <span>
        Signed in as <span className="text-ink">{session.user}</span>
      </span>
      {usage && <span>{formatNumber(usage.tokens_left)} model tokens left today</span>}
      <button type="button" onClick={onSignOut} className="underline hover:text-ink">
        Sign out
      </button>
    </p>
  );
}

export default function App() {
  const [view, setView] = useState(currentView);
  const [session, refresh] = useSession();
  useEffect(() => {
    const onHash = () => setView(currentView());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  if (session === null) return null; // checking the session: render nothing rather than flash the sign-in form
  if (session.auth && !session.user) return <SignIn onSignedIn={refresh} />;
  const signOut = () => postJSON("/api/auth/logout").finally(refresh);
  const View = view.component;
  return (
    <div className="mx-auto max-w-7xl px-5 pb-16 sm:px-8">
      <header className="flex flex-col gap-4 border-b border-rule py-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display text-[1.75rem] leading-none font-bold tracking-tight">Analyst Console</h1>
          <p className="mt-2 text-muted">Answers from governed business metrics, with every step shown.</p>
        </div>
        <div className="flex flex-col gap-3 sm:items-end">
        <Account session={session} onSignOut={signOut} />
        <nav aria-label="Views" className="flex gap-6">
          {VIEWS.map((v) => (
            <a
              key={v.id}
              href={`#${v.id}`}
              aria-current={v.id === view.id ? "page" : undefined}
              className={`border-b-2 pb-1 font-medium ${v.id === view.id ? "border-model text-ink" : "border-transparent text-muted hover:text-ink"}`}
            >
              {v.label}
            </a>
          ))}
        </nav>
        </div>
      </header>
      <main className="pt-8">
        <View />
      </main>
    </div>
  );
}
