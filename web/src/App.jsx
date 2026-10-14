import { useCallback, useEffect, useState } from "react";

import { getJSON, postJSON } from "./lib/api";
import { formatNumber } from "./lib/format";
import { parseHash } from "./lib/route";
import Ask from "./views/Ask";
import Connectors from "./views/Connectors";
import Evals from "./views/Evals";
import History from "./views/History";
import Metrics from "./views/Metrics";
import SignIn from "./views/SignIn";

const VIEWS = [
  { id: "ask", label: "Ask", hint: "Ask a question or replay a run", component: Ask },
  { id: "history", label: "History", hint: "Your questions and what they cost", component: History },
  { id: "metrics", label: "Metrics", hint: "The governed metric catalog", component: Metrics },
  { id: "evals", label: "Evaluations", hint: "How often the agent is right", component: Evals },
  { id: "connectors", label: "Connectors", hint: "Use the analyst from other applications", component: Connectors },
];

function currentRoute() {
  const { view, params } = parseHash();
  return { view: VIEWS.find((v) => v.id === view) ?? VIEWS[0], params };
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

/** Today's shared token budget as a meter: the track is a lighter step of the fill's own hue. */
function BudgetMeter({ usage }) {
  const left = usage.tokens_left / Math.max(usage.daily_tokens, 1);
  return (
    <div className="space-y-1.5">
      <p className="text-sm">{formatNumber(usage.tokens_left)} model tokens left today</p>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-meter-track"
        role="meter"
        aria-label="Model tokens left today"
        aria-valuemin={0}
        aria-valuemax={usage.daily_tokens}
        aria-valuenow={usage.tokens_left}
      >
        <div className={`h-full rounded-full ${left < 0.1 ? "bg-fail" : "bg-series-1"}`} style={{ width: `${left * 100}%` }} />
      </div>
      <p className="text-xs text-muted">
        Shared by everyone · {usage.questions_last_hour} of {usage.questions_per_hour} questions this hour
      </p>
    </div>
  );
}

function Sidebar({ current, session, onSignOut }) {
  const signedIn = session?.auth && session.user;
  return (
    <aside className="border-b border-rule bg-surface lg:fixed lg:inset-y-0 lg:left-0 lg:flex lg:w-60 lg:flex-col lg:border-r lg:border-b-0">
      <div className="px-5 pt-5 pb-3">
        <p className="font-display text-lg leading-none font-bold tracking-tight">Analyst Console</p>
        <p className="mt-1.5 text-xs text-muted">Answers from governed business metrics, every step shown.</p>
      </div>
      <nav aria-label="Views" className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-col lg:overflow-visible">
        {VIEWS.map((v) => (
          <a
            key={v.id}
            href={`#${v.id}`}
            aria-current={v.id === current.id ? "page" : undefined}
            title={v.hint}
            className={`rounded-md px-2.5 py-1.5 text-sm font-medium whitespace-nowrap ${
              v.id === current.id ? "bg-paper text-ink" : "text-muted hover:bg-paper hover:text-ink"
            }`}
          >
            {v.label}
          </a>
        ))}
      </nav>
      {signedIn && (
        <div className="hidden space-y-4 border-t border-rule px-5 py-4 lg:mt-auto lg:block">
          {session.usage && <BudgetMeter usage={session.usage} />}
          <p className="flex items-center justify-between gap-2 text-sm text-muted">
            <span>
              Signed in as <span className="text-ink">{session.user}</span>
            </span>
            <button type="button" onClick={onSignOut} className="underline hover:text-ink">
              Sign out
            </button>
          </p>
        </div>
      )}
    </aside>
  );
}

export default function App() {
  const [route, setRoute] = useState(currentRoute);
  const [session, refresh] = useSession();
  useEffect(() => {
    const onHash = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  if (session === null) return null; // checking the session: render nothing rather than flash the sign-in form
  if (session.auth && !session.user) return <SignIn onSignedIn={refresh} />;
  const signOut = () => postJSON("/api/auth/logout").finally(refresh);
  const { view, params } = route;
  const View = view.component;
  return (
    <div className="min-h-screen">
      <Sidebar current={view} session={session} onSignOut={signOut} />
      <div className="lg:pl-60">
        <header className="flex flex-wrap items-end justify-between gap-2 border-b border-rule px-5 py-4 sm:px-8">
          <div>
            <h1 className="text-xl font-semibold">{view.label}</h1>
            <p className="text-sm text-muted">{view.hint}</p>
          </div>
          {session.auth && session.user && (
            <p className="text-sm text-muted lg:hidden">
              {session.usage && <span>{formatNumber(session.usage.tokens_left)} tokens left · </span>}
              <button type="button" onClick={signOut} className="underline hover:text-ink">
                Sign out
              </button>
            </p>
          )}
        </header>
        <main className="px-5 py-6 pb-16 sm:px-8">
          <View params={params} />
        </main>
      </div>
    </div>
  );
}
