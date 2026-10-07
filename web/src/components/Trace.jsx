import { useState } from "react";

import { STEPS } from "../lib/format";
import { KindTag } from "./bits";
import StepDetails from "./StepDetails";

function Step({ index, event, animate, last }) {
  const [open, setOpen] = useState(event.node === "plan" || event.node === "analyze");
  const info = STEPS[event.node] ?? { title: event.node, kind: "code" };
  const dot = info.kind === "model" ? "bg-model" : "bg-code";
  return (
    <li className={`relative pl-9 ${animate ? "step-in" : ""}`}>
      {!last && <span aria-hidden className="absolute top-7 bottom-0 left-[11px] w-px bg-rule" />}
      <span
        aria-hidden
        className={`absolute top-1.5 left-0 flex h-[23px] w-[23px] items-center justify-center rounded-full text-xs font-semibold text-white ${dot}`}
      >
        {index + 1}
      </span>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-2 rounded-md py-1 text-left"
      >
        <span className="font-display text-[1.02rem] font-semibold">{info.title}</span>
        <KindTag kind={info.kind} />
        <span className="ml-auto text-xs text-muted">{event.elapsed_s.toFixed(1)} s</span>
      </button>
      {open && (
        <div className="mt-1 mb-4 text-[0.92rem] leading-relaxed">
          <StepDetails node={event.node} data={event.data} />
        </div>
      )}
      {!open && <div className="mb-3" />}
    </li>
  );
}

export default function Trace({ steps, animate }) {
  return (
    <ol aria-label="Steps used to build the answer">
      {steps.map((event, index) => (
        <Step key={index} index={index} event={event} animate={animate} last={index === steps.length - 1} />
      ))}
    </ol>
  );
}
