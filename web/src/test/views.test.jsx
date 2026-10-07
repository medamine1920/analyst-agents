import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import Ask from "../views/Ask";
import Evals from "../views/Evals";
import Metrics from "../views/Metrics";
import metrics from "./metrics.json";
import trace from "./trace.json";

const summary = (pass1, all, byQuestion) => ({
  overall: { pass_at_1: pass1, pass_all: all, questions: 2, runs: 6 },
  by_category: {},
  by_question: byQuestion,
  errors: 0,
  pending: 0,
  avg_latency_s: 20,
  avg_tokens: 3400,
});

const RESPONSES = {
  "/api/metrics": metrics,
  "/api/traces": [{ id: trace.id, question: trace.question, label: trace.label, created: trace.created }],
  [`/api/traces/${trace.id}`]: trace,
  "/api/evals": [
    { file: "v3_baseline_x_1.jsonl", agent: "baseline", set: "v3", summary: summary(0.83, 0.5, { h01: "3/3", h06: "2/3" }) },
    { file: "v3_orchestrated_x_1.jsonl", agent: "orchestrated", set: "v3", summary: summary(0.88, 0.88, { h01: "3/3", h06: "0/3" }) },
  ],
  "/api/evals/v3_orchestrated_x_1.jsonl/h06": [
    { repeat: 0, passed: false, failed_checks: [{ number: 0.98 }], answer: "Philadelphia is higher.", error: null, latency_s: 22.5, input_tokens: 3000, output_tokens: 300 },
  ],
};

beforeEach(() => {
  window.matchMedia = () => ({ matches: true }); // reduced motion: replays render instantly
  globalThis.fetch = vi.fn(async (url) => {
    const body = RESPONSES[url];
    return body ? { ok: true, json: async () => body } : { ok: false, status: 404 };
  });
});
afterEach(cleanup);

test("metrics view lists every governed metric and filters by search", async () => {
  render(<Metrics />);
  expect(await screen.findByText("revenue_pre_tax")).toBeTruthy();
  expect(screen.getAllByRole("listitem").length).toBe(12);
  fireEvent.change(screen.getByPlaceholderText(/Search/), { target: { value: "tax" } });
  expect(screen.getAllByRole("listitem").length).toBeLessThan(12);
  fireEvent.change(screen.getByPlaceholderText(/Search/), { target: { value: "zzz" } });
  expect(screen.getByText(/No metric matches/)).toBeTruthy();
});

test("ask view replays a saved run: answer, check badge and every step", async () => {
  render(<Ask />);
  const select = await screen.findByLabelText(/Saved runs replay/);
  await waitFor(() => expect(select.querySelectorAll("option").length).toBe(2));
  fireEvent.change(select, { target: { value: trace.id } });
  expect(await screen.findByRole("heading", { name: trace.question })).toBeTruthy();
  expect(screen.getByText(/every number in this answer comes from the computed results/)).toBeTruthy();
  for (const title of ["Check business terms", "Plan the queries", "Run the queries", "Compute the numbers", "Write the answer"]) {
    expect(screen.getByText(title)).toBeTruthy();
  }
  expect(screen.getAllByText(/Philadelphia/).length).toBeGreaterThan(0);
});

test("evaluations view compares agents and opens the saved answers behind a score", async () => {
  render(<Evals />);
  expect(await screen.findByText("Held-out set (v3)")).toBeTruthy();
  expect(screen.getByText("50%")).toBeTruthy();
  expect(screen.getAllByText("88%").length).toBe(2);
  fireEvent.click(screen.getByRole("button", { name: "0/3" }));
  expect(await screen.findByText(/Missing from the answer/)).toBeTruthy();
});
