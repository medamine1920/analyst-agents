import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { formatNumber } from "../lib/format";
import { Ident } from "./bits";
import ExportMenu from "./ExportMenu";
import FeedbackBar from "./FeedbackBar";
import ResultTabs from "./ResultTabs";

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

/** The agent paused: the user says which governed definition a business term means. */
function ApprovalCard({ approval, onChoose }) {
  const [picks, setPicks] = useState({});
  function pick(term, value) {
    const next = { ...picks, [term]: value };
    setPicks(next);
    if (approval.questions.every((q) => next[q.term])) onChoose(next);
  }
  return (
    <div className="space-y-4 rounded-xl border border-rule bg-surface p-4">
      {approval.questions.map((item) => (
        <fieldset key={item.term} className="space-y-2">
          <legend className="font-medium">
            “{item.term}” has several definitions. Which one do you mean?
          </legend>
          {[...item.options, { metric: "all", label: "Report all of them", description: "" }].map((option) => (
            <button
              key={option.metric}
              type="button"
              aria-pressed={picks[item.term] === option.metric}
              onClick={() => pick(item.term, option.metric)}
              className={`block w-full rounded-lg border px-3 py-2 text-left hover:border-ink ${
                picks[item.term] === option.metric ? "border-ink" : "border-rule"
              }`}
            >
              <span className="font-medium">{option.label}</span>{" "}
              {option.metric !== "all" && <Ident>{option.metric}</Ident>}
              {option.description && <span className="mt-0.5 block text-sm text-muted">{option.description}</span>}
            </button>
          ))}
        </fieldset>
      ))}
      <p className="text-sm text-muted">
        The run is saved while it waits, so nothing is lost if the server restarts.
      </p>
    </div>
  );
}

/** The answer came from an earlier run of the same question: say so, and offer a fresh run. */
function ReusedNote({ reused, onAskAgain }) {
  const when = new Date(reused.saved_at * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md bg-code-tint px-3 py-2 text-sm text-code">
      <span>Saved answer from {when}. It used no model tokens.</span>
      {onAskAgain && (
        <button type="button" onClick={onAskAgain} className="font-medium underline">
          Ask again
        </button>
      )}
    </div>
  );
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard access refused: nothing to do, the text is still on screen
    }
  }
  return (
    <button type="button" onClick={copy} className="rounded-md border border-rule px-2.5 py-1 text-sm hover:border-ink">
      {copied ? "Copied" : "Copy answer"}
    </button>
  );
}

function RunFacts({ done }) {
  return (
    <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted">
      <div>
        <dt className="inline">Time </dt>
        <dd className="inline text-ink tabular-nums">{(done.elapsed_s ?? 0).toFixed(1)} s</dd>
      </div>
      <div>
        <dt className="inline">Model calls </dt>
        <dd className="inline text-ink tabular-nums">{done.usage.llm_calls}</dd>
      </div>
      <div>
        <dt className="inline">Tokens </dt>
        <dd className="inline text-ink tabular-nums">{formatNumber(done.usage.input_tokens + done.usage.output_tokens)}</dd>
      </div>
    </dl>
  );
}

/** The question, then the answer with the numbers and a chart behind it, and what it cost. */
export default function AnswerPanel({ question, steps, done, label, running, approval, onChoose, reused, onAskAgain, onAsk, labels = {}, traceId = null, source = "sample" }) {
  const answer = finalAnswer(steps, done);
  return (
    <section aria-label="Answer" className="space-y-4">
      {question && <h2 className="text-xl leading-snug font-semibold text-balance">{question}</h2>}
      {label && <p className="text-sm text-muted">{label}</p>}
      {approval && !running && <ApprovalCard approval={approval} onChoose={onChoose} />}
      {answer ? (
        <ResultTabs steps={steps} labels={labels} source={source} onAsk={running ? null : onAsk}>
          <div className="prose-answer text-[1.02rem]">
            <Markdown remarkPlugins={[remarkGfm]}>{answer}</Markdown>
          </div>
        </ResultTabs>
      ) : (
        running && !approval && <p className="text-muted">Working on it. Each step appears alongside as it finishes.</p>
      )}
      {reused && !running && <ReusedNote reused={reused} onAskAgain={onAskAgain} />}
      <ReviewBadge steps={steps} />
      {done && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-3">
          {reused ? <span className="text-sm text-muted">Replayed from the saved run.</span> : <RunFacts done={done} />}
          <div className="flex gap-2">
            {answer && <CopyButton text={answer} />}
            {answer && <ExportMenu question={question} steps={steps} traceId={traceId} />}
            {onAskAgain && !reused && (
              <button type="button" onClick={onAskAgain} className="rounded-md border border-rule px-2.5 py-1 text-sm hover:border-ink">
                Ask again
              </button>
            )}
          </div>
        </div>
      )}
      {done && answer && !running && <FeedbackBar traceId={traceId} question={question} />}
    </section>
  );
}
