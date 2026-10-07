import { useEffect, useState } from "react";

import Ask from "./views/Ask";
import Evals from "./views/Evals";
import Metrics from "./views/Metrics";

const VIEWS = [
  { id: "ask", label: "Ask", component: Ask },
  { id: "evals", label: "Evaluations", component: Evals },
  { id: "metrics", label: "Metrics", component: Metrics },
];

function currentView() {
  const id = window.location.hash.replace("#", "");
  return VIEWS.find((v) => v.id === id) ?? VIEWS[0];
}

export default function App() {
  const [view, setView] = useState(currentView);
  useEffect(() => {
    const onHash = () => setView(currentView());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const View = view.component;
  return (
    <div className="mx-auto max-w-7xl px-5 pb-16 sm:px-8">
      <header className="flex flex-col gap-4 border-b border-rule py-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display text-[1.75rem] leading-none font-bold tracking-tight">Analyst Console</h1>
          <p className="mt-2 text-muted">Answers from governed business metrics, with every step shown.</p>
        </div>
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
      </header>
      <main className="pt-8">
        <View />
      </main>
    </div>
  );
}
