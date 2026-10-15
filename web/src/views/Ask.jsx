import { useEffect, useRef, useState } from "react";

import AnswerPanel from "../components/AnswerPanel";
import SourcePicker, { useSource } from "../components/SourcePicker";
import Suggestions from "../components/Suggestions";
import Trace from "../components/Trace";
import { getJSON, streamAsk, streamResume } from "../lib/api";
import { linkTo } from "../lib/route";
import { chooseSource } from "../lib/source";

const EXAMPLES = [
  "Pre-tax revenue jumped in March 2025 compared with February 2025. What drove the increase?",
  "Which store had the higher income?",
  "How many stores do we have, and which of them had no sales?",
];
const SUGGEST_FROM = 3; // characters typed before suggestions appear

function useProject(projectId) {
  const [project, setProject] = useState(null);
  const [earlier, setEarlier] = useState([]);
  const load = () => {
    if (!projectId) {
      setProject(null);
      setEarlier([]);
      return;
    }
    getJSON("/api/projects")
      .then((list) => {
        const found = (list ?? []).find((p) => p.id === projectId) ?? null;
        setProject(found);
        if (found?.source) chooseSource(found.source);
      })
      .catch(() => setProject(null));
    getJSON(`/api/history?project=${encodeURIComponent(projectId)}&limit=20`)
      .then((rows) => setEarlier(rows ?? []))
      .catch(() => setEarlier([]));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [projectId]);
  return [project, earlier, load];
}

/**
 * One page: the question box with its options under it, then the answer and how it was built.
 * #ask?q=... prefills a question, #ask?trace=... opens an earlier run, #ask?project=... works inside a project.
 */
export default function Ask({ params = {} }) {
  const [question, setQuestion] = useState(params.q ?? "");
  const [labels, setLabels] = useState({}); // metric name -> its everyday label, for charts and tiles
  const source = useSource();
  const [project, earlier, reloadProject] = useProject(params.project);
  const [shown, setShown] = useState({ question: "", steps: [], done: null, label: "" });
  const [running, setRunning] = useState(false);
  const [interactive, setInteractive] = useState(true);
  const [approval, setApproval] = useState(null);
  const [reused, setReused] = useState(null); // set when the answer is a saved one
  const [error, setError] = useState("");
  const [traceId, setTraceId] = useState(null); // the saved run behind what is shown, for sending it elsewhere
  const controller = useRef(null);
  const box = useRef(null);

  useEffect(() => {
    const scope = source === "sample" ? "" : `?source=${encodeURIComponent(source)}`;
    getJSON(`/api/metrics${scope}`)
      .then((data) => setLabels(Object.fromEntries((data?.metrics ?? []).map((m) => [m.name, m.label]))))
      .catch(() => {}); // labels are a nicety: charts fall back to metric names
  }, [source]);

  useEffect(() => {
    if (params.q) setQuestion(params.q);
    if (params.trace) open(params.trace);
    if (!params.trace && !params.q) reset("");
    // open only reads the id it is given
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.q, params.trace, params.project]);

  function reset(nextQuestion, label = "") {
    controller.current?.abort();
    setError("");
    setApproval(null);
    setReused(null);
    setTraceId(null);
    setShown({ question: nextQuestion, steps: [], done: null, label });
  }

  /** An earlier run, shown as it was: no model call, no tokens. */
  async function open(id) {
    try {
      const trace = await getJSON(`/api/traces/${id}`);
      reset(trace.question, trace.label || "");
      setTraceId(trace.id);
      if (trace.source) chooseSource(trace.source); // its queries (and their translations) are about this data
      setShown((s) => ({
        ...s,
        steps: trace.events.filter((e) => e.type === "step"),
        done: trace.events.find((e) => e.type === "done") ?? null,
      }));
    } catch (e) {
      setError(e.message);
    }
  }

  /** Run one stretch of a live question: from the start, or from a pause to the end. */
  async function follow(start) {
    setRunning(true);
    controller.current = new AbortController();
    const onEvent = (ev) => {
      if (ev.type === "step") setShown((s) => ({ ...s, steps: [...s.steps, ev] }));
      if (ev.type === "approval") setApproval(ev);
      if (ev.type === "reused") {
        setReused(ev);
        setTraceId(ev.trace_id);
      }
      if (ev.type === "saved") setTraceId(ev.id);
      if (ev.type === "done") setShown((s) => ({ ...s, done: ev }));
      if (ev.type === "saved" || ev.type === "approval" || (ev.type === "done" && ev.reused)) {
        window.dispatchEvent(new Event("analyst:question-finished"));
        reloadProject();
      }
      if (ev.type === "error") setError(`The run stopped: ${ev.message}`);
    };
    try {
      await start(onEvent, controller.current.signal);
    } catch (e) {
      if (e.name !== "AbortError") setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  function run(text, fresh = false) {
    if (text.length < 3 || running) return;
    reset(text);
    return follow((onEvent, signal) => streamAsk(text, interactive, onEvent, signal, fresh, source, params.project));
  }

  function ask(event) {
    event?.preventDefault();
    return run(question.trim());
  }

  function onKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) ask(event); // Shift+Enter for a new line
  }

  function addMetric(metric) {
    setQuestion((q) => (q.trim() ? `${q.trim()} ${metric.label.toLowerCase()}` : metric.label));
    box.current?.focus();
  }

  function choose(choice) {
    const threadId = approval.thread_id;
    setApproval(null);
    return follow((onEvent, signal) => streamResume(threadId, choice, onEvent, signal));
  }

  const typing = question.trim().length >= SUGGEST_FROM;
  return (
    <div className="space-y-8">
      <form onSubmit={ask} className="space-y-3">
        {project && (
          <p className="text-sm text-muted">
            In project <span className="font-medium text-ink">{project.name}</span>
          </p>
        )}
        <div className="rounded-xl border border-rule bg-surface shadow-sm transition-shadow focus-within:shadow-md">
          <label htmlFor="question" className="sr-only">
            Your question
          </label>
          <textarea
            id="question"
            ref={box}
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={source === "sample" ? "Ask about orders, revenue, customers, stores or products…" : "Ask about your data…"}
            className="block w-full resize-none rounded-t-xl bg-transparent px-4 pt-3 pb-1 outline-none"
          />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-rule px-3 py-2">
            <SourcePicker compact />
            <label className="flex items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={interactive} onChange={(e) => setInteractive(e.target.checked)} />
              Ask me which definition I mean
            </label>
            <button
              type="submit"
              disabled={running || question.trim().length < 3}
              className="ml-auto rounded-lg bg-ink px-4 py-1.5 text-sm font-medium text-white transition-opacity disabled:opacity-40"
            >
              {running ? "Answering…" : "Ask"}
            </button>
          </div>
        </div>
        {typing && !running && <Suggestions text={question} onPickQuestion={setQuestion} onPickMetric={addMetric} source={source} />}
        {error && <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>}
      </form>

      {shown.question ? (
        <div className="grid grid-cols-1 gap-8 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          <AnswerPanel
            {...shown}
            running={running}
            approval={approval}
            onChoose={choose}
            reused={reused}
            onAskAgain={() => run(shown.question, true)}
            onAsk={(next) => (setQuestion(next), run(next))}
            labels={labels}
            traceId={traceId}
            source={source}
          />
          <section aria-label="How this answer was built" className="self-start rounded-xl border border-rule bg-surface/70 p-5">
            <h3 className="mb-4 font-semibold">How this answer was built</h3>
            <Trace steps={shown.steps} animate={running} />
            {running && <p className="pl-9 text-sm text-muted">Next step running…</p>}
            {approval && !running && <p className="pl-9 text-sm text-muted">Paused: waiting for your choice.</p>}
          </section>
        </div>
      ) : (
        <Welcome source={source} earlier={earlier} onPick={(q) => (setQuestion(q), box.current?.focus())} />
      )}
    </div>
  );
}

function Welcome({ source, earlier, onPick }) {
  if (earlier.length) {
    return (
      <section className="space-y-2" aria-label="Earlier in this project">
        <h2 className="text-sm font-medium text-muted">Earlier in this project</h2>
        <ul className="divide-y divide-rule rounded-xl border border-rule bg-surface">
          {earlier.map((row, index) => (
            <li key={`${row.asked_at}-${index}`}>
              <a
                href={row.trace_id ? linkTo("ask", { trace: row.trace_id, project: row.project }) : linkTo("ask", { q: row.question, project: row.project })}
                className="flex items-baseline justify-between gap-4 px-4 py-2.5 text-sm hover:bg-paper"
              >
                <span>{row.question}</span>
                <span className="shrink-0 text-xs text-muted">{new Date(row.asked_at * 1000).toLocaleDateString()}</span>
              </a>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  return (
    <section className="max-w-2xl space-y-3 text-muted" aria-label="Getting started">
      <p>
        Ask in plain words. The agent plans the queries, code computes every number, and the answer is checked against
        them before you see it.
      </p>
      {source === "sample" && (
        <div className="space-y-1.5">
          <p className="text-sm">For example:</p>
          {EXAMPLES.map((example) => (
            <button key={example} type="button" onClick={() => onPick(example)} className="block text-left text-sm text-ink underline-offset-2 hover:underline">
              {example}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
