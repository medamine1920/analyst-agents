import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import Trace from "../components/Trace";
import { getJSON, streamAsk } from "../lib/api";
import { formatNumber } from "../lib/format";

const EXAMPLES = [
  "Pre-tax revenue jumped in March 2025 compared with February 2025. What drove the increase?",
  "Which store had the higher income?",
  "How many stores do we have, and which of them had no sales?",
];

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

function finalAnswer(steps, done) {
  if (done?.answer) return done.answer;
  const last = [...steps].reverse().find((s) => s.node === "give_up");
  return last?.data.answer ?? "";
}

function ReviewBadge({ steps }) {
  const reviews = steps.filter((s) => s.node === "review");
  if (!reviews.length) return null;
  const issues = reviews[reviews.length - 1].data.issues ?? [];
  const passed = issues.length === 0;
  return (
    <p className={`rounded-md px-3 py-2 text-sm ${passed ? "bg-code-tint text-code" : "bg-fail-tint text-fail"}`}>
      {passed
        ? "Checked: every number in this answer comes from the computed results."
        : `The final check still found ${issues.length} problem${issues.length > 1 ? "s" : ""}; see step details.`}
    </p>
  );
}

export function AnswerPanel({ question, steps, done, label, running }) {
  const answer = finalAnswer(steps, done);
  return (
    <section aria-label="Answer" className="space-y-4">
      {question && <h2 className="font-display text-2xl leading-tight font-semibold text-balance">{question}</h2>}
      {label && <p className="text-sm text-muted">{label}</p>}
      {answer ? (
        <div className="prose-answer text-[1.02rem]">
          <Markdown remarkPlugins={[remarkGfm]}>{answer}</Markdown>
        </div>
      ) : (
        running && <p className="text-muted">Working on it. Each step appears on the right as it finishes.</p>
      )}
      <ReviewBadge steps={steps} />
      {done && (
        <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted">
          <div>
            <dt className="inline">Time </dt>
            <dd className="inline text-ink">{done.elapsed_s.toFixed(1)} s</dd>
          </div>
          <div>
            <dt className="inline">Model calls </dt>
            <dd className="inline text-ink">{done.usage.llm_calls}</dd>
          </div>
          <div>
            <dt className="inline">Tokens </dt>
            <dd className="inline text-ink">{formatNumber(done.usage.input_tokens + done.usage.output_tokens)}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}

export default function Ask() {
  const [mode, setMode] = useState("replay");
  const [question, setQuestion] = useState("");
  const [traces, setTraces] = useState([]);
  const [shown, setShown] = useState({ question: "", steps: [], done: null, label: "" });
  const [running, setRunning] = useState(false);
  const [animate, setAnimate] = useState(false);
  const [error, setError] = useState("");
  const timers = useRef([]);
  const controller = useRef(null);

  const loadTraces = () => getJSON("/api/traces").then(setTraces).catch((e) => setError(e.message));
  useEffect(() => {
    loadTraces();
    return () => timers.current.forEach(clearTimeout);
  }, []);

  function reset(nextQuestion, label = "") {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    controller.current?.abort();
    setError("");
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

  async function askLive(event) {
    event.preventDefault();
    const text = question.trim();
    if (text.length < 3) return;
    reset(text);
    setAnimate(true);
    setRunning(true);
    controller.current = new AbortController();
    try {
      await streamAsk(
        text,
        (ev) => {
          if (ev.type === "step") setShown((s) => ({ ...s, steps: [...s.steps, ev] }));
          if (ev.type === "done") setShown((s) => ({ ...s, done: ev }));
          if (ev.type === "saved") loadTraces();
          if (ev.type === "error") setError(`The run stopped: ${ev.message}`);
        },
        controller.current.signal,
      );
    } catch (e) {
      if (e.name !== "AbortError") setError(e.message);
    } finally {
      setRunning(false);
    }
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
        <div className="grid gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <AnswerPanel {...shown} running={running} />
          <section aria-label="How this answer was built" className="rounded-xl border border-rule bg-surface/70 p-5">
            <h3 className="mb-4 font-display text-lg font-semibold">How this answer was built</h3>
            <Trace steps={shown.steps} animate={animate} />
            {running && <p className="pl-9 text-sm text-muted">Next step running…</p>}
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
