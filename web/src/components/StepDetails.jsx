import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { DataTable, Ident } from "./bits";

function Resolve({ data }) {
  const { ambiguities = [], resolved = [] } = data.resolution ?? {};
  if (!ambiguities.length && !resolved.length) {
    return <p>No ambiguous business terms in the question.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {ambiguities.map((item) => (
        <li key={item.term}>
          <strong>“{item.term}”</strong> has several definitions:{" "}
          {item.metrics.map((m) => <Ident key={m}>{m}</Ident>).reduce((a, b) => [a, " and ", b])}. All of them will be reported.
        </li>
      ))}
      {resolved.map((item) => (
        <li key={item.term}>
          <strong>“{item.term}”</strong> means {item.metrics.map((m) => <Ident key={m}>{m}</Ident>)} in this question.
        </li>
      ))}
    </ul>
  );
}

function period(step) {
  if (!step.start_date && !step.end_date) return "all dates";
  return `${step.start_date ?? "start"} to ${step.end_date ?? "end"}`;
}

function PlanView({ data }) {
  if (data.error) return <p className="text-fail">{data.error}</p>;
  const plan = data.plan;
  if (!plan) return null;
  return (
    <div className="space-y-3">
      {data.attempts > 1 && (
        <p className="text-sm text-muted">Valid on attempt {data.attempts}: earlier plans were sent back with their errors.</p>
      )}
      <ol className="space-y-2">
        {plan.steps.map((step) => (
          <li key={step.id} className="rounded-md border border-rule bg-surface p-2.5">
            <p className="font-medium">{step.purpose || step.id}</p>
            <p className="mt-1 flex flex-wrap items-center gap-1.5 text-sm text-muted">
              {step.metrics.map((m) => <Ident key={m}>{m}</Ident>)}
              {step.group_by.length > 0 && <span>by</span>}
              {step.group_by.map((g) => <Ident key={g}>{g}</Ident>)}
              <span>over {period(step)}</span>
            </p>
          </li>
        ))}
      </ol>
      {plan.comparisons?.length > 0 && (
        <p className="text-sm">
          Compare {plan.comparisons.map((c) => `${c.before_step} with ${c.after_step}`).join("; ")}.
        </p>
      )}
      {plan.calculations?.map((c) => (
        <p key={c.name} className="text-sm">
          Formula <Ident>{c.name}</Ident> = <Ident>{c.expression}</Ident>
        </p>
      ))}
    </div>
  );
}

function Execute({ data }) {
  return (
    <div className="space-y-1.5">
      <ul className="space-y-1">
        {Object.entries(data.queries ?? {}).map(([step, q]) => (
          <li key={step}>
            <Ident>{step}</Ident> returned {q.rows} {q.rows === 1 ? "row" : "rows"}
          </li>
        ))}
      </ul>
      {data.errors?.map((e) => (
        <p key={e} className="text-sm text-fail">{e}</p>
      ))}
    </div>
  );
}

function Analyze({ data }) {
  const facts = data.facts ?? {};
  return (
    <div className="space-y-4">
      {facts.steps?.map((step) => (
        <section key={step.id} className="space-y-1.5">
          <h4 className="text-sm font-semibold">{step.purpose || step.id}</h4>
          <DataTable rows={step.rows} />
          {step.totals && (
            <>
              <p className="text-xs text-muted">Totals across all groups</p>
              <DataTable rows={step.totals} />
            </>
          )}
        </section>
      ))}
      {facts.comparisons?.map((c) => (
        <section key={`${c.before}-${c.after}`} className="space-y-1.5">
          <h4 className="text-sm font-semibold">
            Change from <Ident>{c.before}</Ident> to <Ident>{c.after}</Ident>
          </h4>
          <DataTable rows={c.rows} />
        </section>
      ))}
      {facts.groups_missing_between_steps?.map((m) => (
        <p key={`${m.present_in}-${m.absent_from}`} className="text-sm">
          In <Ident>{m.present_in}</Ident> but not in <Ident>{m.absent_from}</Ident>: <strong>{m.groups.join(", ")}</strong>
        </p>
      ))}
      {facts.groups_without_values?.map((g) => (
        <p key={`${g.step}-${g.metric}`} className="text-sm">
          No <Ident>{g.metric}</Ident> for: <strong>{g.groups.join(", ")}</strong>
        </p>
      ))}
      {facts.calculation_errors?.map((e) => (
        <p key={e.calculation} className="text-sm text-fail">
          Formula {e.calculation} could not run: {e.error}
        </p>
      ))}
    </div>
  );
}

function Write({ data }) {
  return (
    <div className="space-y-1.5">
      {data.attempt > 1 && <p className="text-sm text-muted">Rewrite {data.attempt - 1}, after the check below found problems.</p>}
      <div className="prose-answer rounded-md border border-rule bg-surface p-3 text-sm text-muted">
        <Markdown remarkPlugins={[remarkGfm]}>{data.draft}</Markdown>
      </div>
    </div>
  );
}

function Review({ data }) {
  if (!data.issues?.length) {
    return <p>Every number in the draft appears in the computed numbers, and every required definition is named.</p>;
  }
  return (
    <ul className="space-y-1 text-fail">
      {data.issues.map((issue) => (
        <li key={issue}>{issue}</li>
      ))}
    </ul>
  );
}

const VIEWS = { resolve: Resolve, plan: PlanView, execute: Execute, analyze: Analyze, analyze_partial: Analyze, write: Write, review: Review };

export default function StepDetails({ node, data }) {
  const View = VIEWS[node];
  if (!View) return <p>{data.answer ?? JSON.stringify(data)}</p>;
  return <View data={data} />;
}
