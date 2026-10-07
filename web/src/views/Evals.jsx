import { Fragment, useEffect, useMemo, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { getJSON } from "../lib/api";
import { formatNumber, percent } from "../lib/format";

const SET_NAMES = { v2: "Multi-step set (v2)", v3: "Held-out set (v3)", v1: "Single-step set (v1)" };
const SET_ORDER = ["v3", "v2", "v1"];

function setOf(run) {
  return run.set || run.file.split("_")[0];
}

/** Keep the most recent results file for each set and agent (file names carry a timestamp). */
function latestRuns(runs) {
  const latest = {};
  for (const run of [...runs].sort((a, b) => a.file.localeCompare(b.file))) {
    latest[`${setOf(run)}|${run.agent}`] = run;
  }
  return Object.values(latest);
}

function scoreStyle(score) {
  const [passed, total] = score.split("/").map(Number);
  if (passed === total) return "bg-code-tint text-code";
  if (passed === 0) return "bg-fail-tint text-fail";
  return "bg-partial-tint text-partial";
}

function RunDetails({ file, questionId }) {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setRuns(null);
    getJSON(`/api/evals/${encodeURIComponent(file)}/${questionId}`).then(setRuns).catch((e) => setError(e.message));
  }, [file, questionId]);
  if (error) return <p className="text-fail">{error}</p>;
  if (!runs) return <p className="text-muted">Loading the saved answers…</p>;
  return (
    <ol className="space-y-3">
      {runs.map((run) => (
        <li key={run.repeat} className="rounded-lg border border-rule bg-surface p-3">
          <p className="text-sm font-semibold">
            Run {run.repeat + 1}:{" "}
            <span className={run.passed ? "text-code" : "text-fail"}>
              {run.passed ? "passed" : run.error ? "stopped with an error" : "failed"}
            </span>
            <span className="ml-3 font-normal text-muted">
              {run.latency_s?.toFixed(1)} s, {formatNumber((run.input_tokens ?? 0) + (run.output_tokens ?? 0))} tokens
            </span>
          </p>
          {run.failed_checks?.length > 0 && (
            <p className="mt-1 text-sm text-fail">Missing from the answer: {run.failed_checks.map((c) => JSON.stringify(c)).join(", ")}</p>
          )}
          {run.error && <p className="mt-1 text-sm text-fail">{run.error.slice(0, 300)}</p>}
          {run.answer && (
            <details className="mt-2">
              <summary className="cursor-pointer text-sm text-muted">Show the answer</summary>
              <div className="prose-answer mt-2 text-sm">
                <Markdown remarkPlugins={[remarkGfm]}>{run.answer}</Markdown>
              </div>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}

function SetSection({ setName, runs }) {
  const [selected, setSelected] = useState(null);
  const agents = runs.map((r) => r.agent).sort();
  const byAgent = Object.fromEntries(runs.map((r) => [r.agent, r]));
  const questions = [...new Set(runs.flatMap((r) => Object.keys(r.summary.by_question)))].sort();
  return (
    <section className="space-y-4">
      <h2 className="font-display text-xl font-semibold">{SET_NAMES[setName] ?? setName}</h2>
      <div className="overflow-x-auto rounded-xl border border-rule bg-surface">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="text-left text-muted">
              <th className="px-3 py-2 font-medium">Agent</th>
              <th className="px-3 py-2 text-right font-medium">Accuracy (pass@1)</th>
              <th className="px-3 py-2 text-right font-medium">Right on every run</th>
              <th className="px-3 py-2 text-right font-medium">Tokens per question</th>
              <th className="px-3 py-2 text-right font-medium">Seconds per question</th>
              <th className="px-3 py-2 text-right font-medium">Errors</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => {
              const s = byAgent[agent].summary;
              return (
                <tr key={agent} className="border-t border-rule">
                  <td className="px-3 py-2 font-medium capitalize">{agent}</td>
                  <td className="px-3 py-2 text-right font-display text-base font-semibold">{percent(s.overall.pass_at_1)}</td>
                  <td className="px-3 py-2 text-right font-display text-base font-semibold">{percent(s.overall.pass_all)}</td>
                  <td className="px-3 py-2 text-right">{formatNumber(Math.round(s.avg_tokens))}</td>
                  <td className="px-3 py-2 text-right">{s.avg_latency_s.toFixed(1)}</td>
                  <td className="px-3 py-2 text-right">
                    {s.errors}
                    {s.pending > 0 && <span className="text-muted"> ({s.pending} not yet run)</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div>
        <p className="mb-2 text-sm text-muted">Runs passed per question. Select a score to read the saved answers.</p>
        <div
          className="inline-grid gap-1.5 text-sm"
          style={{ gridTemplateColumns: `auto repeat(${agents.length}, minmax(6.5rem, auto))` }}
        >
          <span />
          {agents.map((agent) => (
            <span key={agent} className="px-2 text-center text-muted capitalize">{agent}</span>
          ))}
          {questions.map((qid) => (
            <Fragment key={qid}>
              <span className="pr-3 font-mono text-xs leading-8 text-muted">{qid}</span>
              {agents.map((agent) => {
                const score = byAgent[agent].summary.by_question[qid];
                if (!score) return <span key={agent} className="text-center leading-8 text-muted">not run</span>;
                const active = selected?.qid === qid && selected?.agent === agent;
                return (
                  <button
                    key={agent}
                    type="button"
                    onClick={() => setSelected(active ? null : { qid, agent })}
                    aria-pressed={active}
                    className={`rounded-md px-2 py-1 font-semibold ${scoreStyle(score)} ${active ? "ring-2 ring-ink" : ""}`}
                  >
                    {score}
                  </button>
                );
              })}
            </Fragment>
          ))}
        </div>
      </div>

      {selected && (
        <div className="space-y-2">
          <h3 className="font-display text-base font-semibold">
            {selected.qid}, {selected.agent} agent
          </h3>
          <RunDetails file={byAgent[selected.agent].file} questionId={selected.qid} />
        </div>
      )}
    </section>
  );
}

export default function Evals() {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getJSON("/api/evals").then(setRuns).catch((e) => setError(e.message));
  }, []);
  const bySet = useMemo(() => {
    const groups = {};
    for (const run of latestRuns(runs ?? [])) (groups[setOf(run)] ??= []).push(run);
    return groups;
  }, [runs]);

  if (error) return <p role="alert" className="rounded-md bg-fail-tint px-3 py-2 text-fail">{error}</p>;
  if (!runs) return <p className="text-muted">Loading evaluation results…</p>;
  if (!runs.length) {
    return <p className="text-muted">No results yet. Run an evaluation with python -m evals.run_eval to see it here.</p>;
  }
  return (
    <div className="space-y-12">
      <p className="max-w-2xl text-muted">
        Each question has an answer verified with plain SQL and runs three times. Accuracy is the share of all runs that
        were right; the stricter measure counts only questions answered right on every run.
      </p>
      {SET_ORDER.filter((name) => bySet[name]).map((name) => (
        <SetSection key={name} setName={name} runs={bySet[name]} />
      ))}
    </div>
  );
}
