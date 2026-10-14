import { useEffect, useRef, useState } from "react";

import AnswerPanel from "../components/AnswerPanel";
import Suggestions from "../components/Suggestions";
import Trace from "../components/Trace";
import { getJSON, streamAsk, streamResume } from "../lib/api";

const EXAMPLES = [
  "Pre-tax revenue jumped in March 2025 compared with February 2025. What drove the increase?",
  "Which store had the higher income?",
  "How many stores do we have, and which of them had no sales?",
];

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/** params come from the address: #ask?q=... prefills a live question, #ask?trace=... opens a saved run. */
export default function Ask({ params = {} }) {
  const [mode, setMode] = useState(params.mode === "live" || params.q ? "live" : "replay");
  const [question, setQuestion] = useState(params.q ?? "");
  const [labels, setLabels] = useState({}); // metric name -> its everyday label, for charts and tiles
  const [traces, setTraces] = useState([]);
  const [shown, setShown] = useState({ question: "", steps: [], done: null, label: "" });
  const [running, setRunning] = useState(false);
  const [interactive, setInteractive] = useState(true);
  const [approval, setApproval] = useState(null);
  const [reused, setReused] = useState(null); // set when the answer is a saved one
  const [animate, setAnimate] = useState(false);
  const [error, setError] = useState("");
  const timers = useRef([]);
  const controller = useRef(null);

  const loadTraces = () => getJSON("/api/traces").then(setTraces).catch((e) => setError(e.message));
  useEffect(() => {
    loadTraces();
    getJSON("/api/metrics")
      .then((data) => setLabels(Object.fromEntries((data?.metrics ?? []).map((m) => [m.name, m.label]))))
      .catch(() => {}); // labels are a nicety: charts fall back to metric names
    return () => timers.current.forEach(clearTimeout);
  }, []);

  useEffect(() => {
    if (params.q) {
      setMode("live");
      setQuestion(params.q);
    }
    if (params.mode === "live") setMode("live");
    if (params.trace) {
      setMode("replay");
      replay(params.trace);
    }
    // replay is stable enough for this: it only reads the id it is given
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.q, params.trace, params.mode]);

  function reset(nextQuestion, label = "") {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    controller.current?.abort();
    setError("");
    setApproval(null);
    setReused(null);
    setShown({ question: nextQuestion, steps: [], done: null, label });
  }

  async function replay(id) {
    if (!id) return;
    try {
      const trace = await getJSON(`/api/traces/${id}`);
      reset(trace.question, trace.label || "Saved run, replayed without calling the model.");
      const steps = trace.events.filter((e) => e.type === "step");
      const done = trace.events.find((e) => e.type === "done") ?? null;
      const instant = reducedMotion();
      setAnimate(!instant);
      setRunning(!instant);
      if (instant) {
        setShown((s) => ({ ...s, steps, done }));
        return;
      }
      steps.forEach((step, index) => {
        timers.current.push(setTimeout(() => setShown((s) => ({ ...s, steps: [...s.steps, step] })), 450 * (index + 1)));
      });
      timers.current.push(
        setTimeout(() => {
          setShown((s) => ({ ...s, done }));
          setRunning(false);
        }, 450 * (steps.length + 1)),
      );
    } catch (e) {
      setError(e.message);
    }
  }

  /** Run one stretch of a live question: from the start, or from a pause to the end. */
  async function follow(start) {
    setAnimate(true);
    setRunning(true);
    controller.current = new AbortController();
    const onEvent = (ev) => {
      if (ev.type === "step") setShown((s) => ({ ...s, steps: [...s.steps, ev] }));
      if (ev.type === "approval") setApproval(ev);
      if (ev.type === "reused") setReused(ev);
      if (ev.type === "done") setShown((s) => ({ ...s, done: ev }));
      if (ev.type === "saved") loadTraces();
      if (ev.type === "saved" || ev.type === "approval") window.dispatchEvent(new Event("analyst:question-finished"));
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

  function askLive(event, fresh = false) {
    event?.preventDefault();
    const text = question.trim();
    if (text.length < 3) return;
    reset(text);
    return follow((onEvent, signal) => streamAsk(text, interactive, onEvent, signal, fresh));
  }

  function addMetric(metric) {
    setQuestion((q) => (q.trim() ? `${q.trim()} ${metric.label.toLowerCase()}` : metric.label));
  }

  function choose(choice) {
    const threadId = approval.thread_id;
    setApproval(null);
    return follow((onEvent, signal) => streamResume(threadId, choice, onEvent, signal));
  }

  return (
    <div className="space-y-8">
      <div className="space-y-3">
        <div role="radiogroup" aria-label="Run mode" className="inline-flex rounded-lg border border-rule bg-surface p-1 text-sm">
          {[
            ["replay", "Replay a saved run"],
            ["live", "Ask the agent live"],
          ].map(([value, text]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={mode === value}
              onClick={() => setMode(value)}
              className={`rounded-md px-3 py-1.5 ${mode === value ? "bg-ink text-white" : "text-muted hover:text-ink"}`}
            >
              {text}
            </button>
          ))}
        </div>

        {mode === "live" ? (
          <form onSubmit={askLive} className="space-y-2">
            <label htmlFor="question" className="block text-sm text-muted">
              Ask about orders, revenue, customers, stores or products (September 2024 to August 2025).
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <textarea
                id="question"
                rows={2}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Which store had the higher average order value in 2025?"
                className="min-h-[3.25rem] flex-1 resize-y rounded-lg border border-rule bg-surface px-3 py-2"
              />
              <button
                type="submit"
                disabled={running || question.trim().length < 3}
                className="rounded-lg bg-model px-5 py-2 font-medium text-white disabled:opacity-50"
              >
                {running ? "Answering…" : "Ask"}
              </button>
            </div>
            <label className="flex items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={interactive} onChange={(e) => setInteractive(e.target.checked)} />
              Ask me when a business term has several definitions (otherwise all of them are reported).
            </label>
            <Suggestions text={question} onPickQuestion={setQuestion} onPickMetric={addMetric} />
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setQuestion(example)}
                  className="rounded-full border border-rule bg-surface px-3 py-1 text-left text-sm text-muted hover:text-ink"
                >
                  {example}
                </button>
              ))}
            </div>
          </form>
        ) : (
          <div className="space-y-1.5">
            <label htmlFor="trace" className="block text-sm text-muted">
              Saved runs replay step by step without using any tokens.
            </label>
            <select
              id="trace"
              defaultValue=""
              onChange={(e) => replay(e.target.value)}
              className="w-full max-w-2xl rounded-lg border border-rule bg-surface px-3 py-2"
            >
              <option value="" disabled>
                {traces.length ? "Choose a saved run" : "No saved runs yet: ask a question live to record one"}
              </option>
              {traces.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label ? "Demo: " : ""}
                  {t.question}
                </option>
              ))}
            </select>
          </div>
        )}
        {error && <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-sm text-fail">{error}</p>}
      </div>

      {shown.question ? (
        <div className="grid grid-cols-1 gap-8 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          <AnswerPanel
            {...shown}
            running={running}
            approval={approval}
            onChoose={choose}
            reused={reused}
            onAskAgain={mode === "live" ? () => askLive(null, true) : null}
            labels={labels}
          />
          <section aria-label="How this answer was built" className="self-start rounded-xl border border-rule bg-surface/70 p-5">
            <h3 className="mb-4 font-semibold">How this answer was built</h3>
            <Trace steps={shown.steps} animate={animate} />
            {running && <p className="pl-9 text-sm text-muted">Next step running…</p>}
            {approval && !running && <p className="pl-9 text-sm text-muted">Paused: waiting for your choice.</p>}
          </section>
        </div>
      ) : (
        <p className="max-w-xl text-muted">
          Pick a saved run to see how the agent turns a question into a checked answer: violet steps are the model
          thinking, green steps are code doing the work.
        </p>
      )}
    </div>
  );
}
