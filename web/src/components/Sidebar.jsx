import { useCallback, useEffect, useState } from "react";

import { getJSON, postJSON } from "../lib/api";
import { linkTo } from "../lib/route";

function useList(path) {
  const [items, setItems] = useState([]);
  const load = useCallback(() => getJSON(path).then((list) => setItems(list ?? [])).catch(() => setItems([])), [path]);
  useEffect(() => {
    load();
    window.addEventListener("analyst:question-finished", load);
    window.addEventListener("analyst:projects-changed", load);
    return () => {
      window.removeEventListener("analyst:question-finished", load);
      window.removeEventListener("analyst:projects-changed", load);
    };
  }, [load]);
  return items;
}

function Section({ title, action, children }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between px-2.5 pt-3">
        <span className="text-xs font-medium tracking-wide text-muted uppercase">{title}</span>
        {action}
      </div>
      {children}
    </div>
  );
}

function Item({ href, active, children, title }) {
  return (
    <a
      href={href}
      title={title}
      aria-current={active ? "page" : undefined}
      className={`block truncate rounded-md px-2.5 py-1.5 text-sm transition-colors ${
        active ? "bg-paper font-medium text-ink" : "text-muted hover:bg-paper hover:text-ink"
      }`}
    >
      {children}
    </a>
  );
}

function NewProject({ onDone }) {
  const [name, setName] = useState("");
  async function submit(event) {
    event.preventDefault();
    if (!name.trim()) return onDone();
    const project = await postJSON("/api/projects", { name: name.trim() }).catch(() => null);
    window.dispatchEvent(new Event("analyst:projects-changed"));
    onDone();
    if (project) window.location.hash = linkTo("ask", { project: project.id });
  }
  return (
    <form onSubmit={submit} className="px-1.5">
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={submit}
        onKeyDown={(e) => e.key === "Escape" && onDone()}
        placeholder="Project name"
        aria-label="New project name"
        maxLength={60}
        className="w-full rounded-md border border-rule bg-surface px-2 py-1 text-sm"
      />
    </form>
  );
}

/** Projects first, then recent questions, then everything else; the account at the bottom. */
export default function Sidebar({ route, session, onSignOut }) {
  const projects = useList("/api/projects");
  const history = useList("/api/history?limit=30");
  // Each question once, newest first: asking it again should not push the others out.
  const recent = history.filter((row, index) => history.findIndex((r) => r.question === row.question) === index).slice(0, 8);
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState(false); // small screens: the menu is folded away
  const at = (view, extra = {}) => route.view === view && Object.entries(extra).every(([k, v]) => route.params[k] === v);

  useEffect(() => setOpen(false), [route]);

  return (
    <aside className="border-b border-rule bg-surface lg:fixed lg:inset-y-0 lg:left-0 lg:flex lg:w-64 lg:flex-col lg:border-r lg:border-b-0">
      <div className="flex items-center justify-between px-4 py-3 lg:block lg:px-5 lg:pt-5">
        <a href="#ask" className="block">
          <span className="font-display text-lg leading-none font-bold tracking-tight">Analyst Console</span>
          <span className="mt-1 hidden text-xs text-muted lg:block">Answers checked against governed metrics</span>
        </a>
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="rounded-md border border-rule px-2.5 py-1 text-sm lg:hidden">
          {open ? "Close" : "Menu"}
        </button>
      </div>
      <nav aria-label="Views" className={`${open ? "block" : "hidden"} space-y-1 overflow-y-auto px-2.5 pb-4 lg:block lg:flex-1`}>
        <a
          href="#ask"
          className="mb-2 block rounded-lg border border-rule px-3 py-2 text-sm font-medium transition-colors hover:border-ink"
        >
          + New question
        </a>
        <Section
          title="Projects"
          action={
            <button type="button" onClick={() => setAdding(true)} aria-label="New project" className="rounded px-1.5 text-muted hover:text-ink">
              +
            </button>
          }
        >
          {adding && <NewProject onDone={() => setAdding(false)} />}
          {projects.length === 0 && !adding && (
            <button type="button" onClick={() => setAdding(true)} className="px-2.5 text-left text-xs text-muted hover:text-ink">
              Group questions by topic: create a project
            </button>
          )}
          {projects.map((p) => (
            <Item key={p.id} href={linkTo("ask", { project: p.id })} active={at("ask", { project: p.id })} title={p.name}>
              {p.name}
              <span className="ml-1.5 text-xs text-muted">{p.questions || ""}</span>
            </Item>
          ))}
        </Section>
        {recent.length > 0 && (
          <Section title="Recent">
            {recent.map((row, index) => (
              <Item
                key={`${row.asked_at}-${index}`}
                href={row.trace_id ? linkTo("ask", { trace: row.trace_id }) : linkTo("ask", { q: row.question })}
                active={row.trace_id && at("ask", { trace: row.trace_id })}
                title={row.question}
              >
                {row.question}
              </Item>
            ))}
            <Item href="#history" active={at("history")}>
              All questions
            </Item>
          </Section>
        )}
        <Section title="Data">
          <Item href="#sources" active={at("sources")}>Data sources</Item>
          <Item href="#connections" active={at("connections")}>Connections</Item>
          <Item href="#metrics" active={at("metrics")}>Metrics</Item>
        </Section>
        <Section title="Settings">
          <Item href="#settings" active={at("settings")}>Settings</Item>
        </Section>
      </nav>
      {session?.auth && session.user && (
        <div className={`${open ? "flex" : "hidden"} items-center justify-between gap-2 border-t border-rule px-5 py-3 text-sm lg:flex`}>
          <a href={linkTo("settings", { tab: "profile" })} className="truncate text-ink hover:underline">
            {session.user}
          </a>
          <button type="button" onClick={onSignOut} className="text-muted underline hover:text-ink">
            Sign out
          </button>
        </div>
      )}
    </aside>
  );
}
