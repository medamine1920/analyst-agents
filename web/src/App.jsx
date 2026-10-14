import { useCallback, useEffect, useState } from "react";

import Sidebar from "./components/Sidebar";
import { getJSON, postJSON } from "./lib/api";
import { linkTo, parseHash } from "./lib/route";
import { PasswordForm } from "./views/Account";
import Ask from "./views/Ask";
import Connections from "./views/Connections";
import History from "./views/History";
import Metrics from "./views/Metrics";
import Settings from "./views/Settings";
import SignIn from "./views/SignIn";
import Sources from "./views/Sources";

const VIEWS = {
  ask: { title: "Ask", component: Ask },
  history: { title: "All questions", hint: "Everything you asked, with what each answer cost", component: History },
  metrics: { title: "Metrics", hint: "What the agent can measure, and how each metric is defined", component: Metrics },
  sources: { title: "Data sources", hint: "Upload files or connect a database, then ask about it", component: Sources },
  connections: { title: "Connections", hint: "Bring data in from the tools you use, and send results out", component: Connections },
  settings: { title: "Settings", component: Settings },
};
// Old addresses keep working: they now live under Settings.
const MOVED = { connectors: "keys", account: "security", admin: "people", evals: "developer" };

function currentRoute() {
  const { view, params } = parseHash();
  if (MOVED[view]) return { view: "settings", params: { tab: MOVED[view] } };
  return { view: VIEWS[view] ? view : "ask", params };
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
  if (session.must_change_password) {
    return (
      <div className="mx-auto mt-16 max-w-sm space-y-4 px-5">
        <h1 className="font-display text-[1.75rem] leading-none font-bold tracking-tight">Welcome, {session.user}</h1>
        <PasswordForm forced onChanged={refresh} />
      </div>
    );
  }
  const signOut = () => postJSON("/api/auth/logout").finally(refresh);
  const view = VIEWS[route.view];
  const View = view.component;
  return (
    <div className="min-h-screen">
      <Sidebar route={route} session={session} onSignOut={signOut} />
      <div className="lg:pl-64">
        {route.view !== "ask" && (
          <header className="border-b border-rule px-5 py-4 sm:px-8">
            <p className="text-xs text-muted">
              <a href={linkTo("ask")} className="hover:underline">Analyst Console</a> / {view.title}
            </p>
            <h1 className="text-xl font-semibold">{view.title}</h1>
            {view.hint && <p className="text-sm text-muted">{view.hint}</p>}
          </header>
        )}
        <main className={`mx-auto px-5 py-6 pb-16 sm:px-8 ${route.view === "ask" ? "max-w-6xl pt-10" : ""}`}>
          <View params={route.params} session={session} onSessionChanged={refresh} />
        </main>
      </div>
    </div>
  );
}
