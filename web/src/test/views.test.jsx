import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "../App";
import Ask from "../views/Ask";
import Evals from "../views/Evals";
import History from "../views/History";
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
  const metricRows = () => screen.getAllByRole("row").length - 1; // minus the header row
  expect(metricRows()).toBe(12);
  expect(screen.getAllByRole("link", { name: "Ask about this" })[0].getAttribute("href")).toMatch(/^#ask\?mode=live&q=/);
  fireEvent.change(screen.getByPlaceholderText(/Search/), { target: { value: "tax" } });
  expect(metricRows()).toBeLessThan(12);
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

/** A fetch response whose body streams the given events as server-sent events. */
function sseResponse(events) {
  const chunks = [new TextEncoder().encode(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""))];
  const read = async () => (chunks.length ? { value: chunks.shift(), done: false } : { value: undefined, done: true });
  return { ok: true, body: { getReader: () => ({ read }) } };
}

test("a live question pauses on an ambiguous term and resumes with the chosen definition", async () => {
  const steps = trace.events.filter((e) => e.type === "step");
  const done = trace.events.find((e) => e.type === "done");
  const approval = {
    type: "approval",
    thread_id: "a".repeat(32),
    elapsed_s: 2,
    questions: [
      {
        term: "income",
        options: [
          { metric: "revenue_pre_tax", label: "Revenue (pre-tax)", description: "Order subtotals." },
          { metric: "revenue_with_tax", label: "Revenue (with tax)", description: "Order totals." },
        ],
      },
    ],
  };
  const clarify = { type: "step", node: "clarify", elapsed_s: 2, data: { choices: { income: "revenue_with_tax" }, plan: steps[1].data.plan } };
  const posts = [];
  globalThis.fetch = vi.fn(async (url, options) => {
    if (!options) return { ok: true, json: async () => [] };
    posts.push([url, JSON.parse(options.body)]);
    return url === "/api/ask" ? sseResponse([...steps.slice(0, 2), approval]) : sseResponse([clarify, ...steps.slice(2), done]);
  });

  render(<Ask />);
  fireEvent.click(screen.getByRole("radio", { name: "Ask the agent live" }));
  fireEvent.change(screen.getByLabelText(/Ask about orders/), { target: { value: "Which store had the higher income?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByText(/has several definitions. Which one do you mean/)).toBeTruthy();
  expect(screen.getByText(/Paused: waiting for your choice/)).toBeTruthy();
  expect(posts[0]).toEqual(["/api/ask", { question: "Which store had the higher income?", interactive: true }]);

  fireEvent.click(screen.getByRole("button", { name: /Revenue \(with tax\)/ }));
  expect(await screen.findByText("Ask which definition is meant")).toBeTruthy();
  expect(posts[1]).toEqual(["/api/resume", { thread_id: "a".repeat(32), choice: { income: "revenue_with_tax" } }]);
  await waitFor(() => expect(screen.getByText(/every number in this answer comes from the computed results/)).toBeTruthy());
  expect(screen.queryByText(/Which one do you mean/)).toBeNull();
});

test("the console asks for sign-in, then shows the account and today's budget", async () => {
  let signedIn = false;
  const posts = [];
  globalThis.fetch = vi.fn(async (url, options) => {
    if (options?.method === "POST") {
      posts.push([url, JSON.parse(options.body)]);
      if (url === "/api/auth/login") {
        const ok = JSON.parse(options.body).password === "correct horse battery";
        signedIn = ok;
        return ok
          ? { ok: true, json: async () => ({ user: "ana" }) }
          : { ok: false, status: 401, json: async () => ({ detail: "Wrong name or password." }) };
      }
    }
    if (url === "/api/auth/me") {
      const usage = { tokens_left: 145750, daily_tokens: 150000 };
      return { ok: true, json: async () => (signedIn ? { user: "ana", auth: true, usage } : { user: null, auth: true }) };
    }
    const body = RESPONSES[url];
    return body ? { ok: true, json: async () => body } : { ok: false, status: 404, json: async () => ({}) };
  });

  render(<App />);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "ana" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong password!" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText("Wrong name or password.")).toBeTruthy();

  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText("145,750 model tokens left today")).toBeTruthy();
  expect(screen.getByText("ana")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Ask" })).toBeTruthy();
  expect(posts[0][1]).toEqual({ name: "ana", password: "wrong password!" });
});

test("a repeated question shows the saved answer, and Ask again runs it fresh", async () => {
  const steps = trace.events.filter((e) => e.type === "step");
  const done = trace.events.find((e) => e.type === "done");
  const reusedNote = { type: "reused", saved_at: 1791900000, trace_id: trace.id, question: trace.question };
  const posts = [];
  globalThis.fetch = vi.fn(async (url, options) => {
    if (url.startsWith("/api/suggestions")) {
      const body = { questions: [{ question: trace.question, times_asked: 3, saved_answer: true }], metrics: [] };
      return { ok: true, json: async () => body };
    }
    if (!options) return { ok: true, json: async () => [] };
    const body = JSON.parse(options.body);
    posts.push(body);
    return body.fresh ? sseResponse([...steps, done]) : sseResponse([...steps, reusedNote, { ...done, reused: true }]);
  });

  render(<Ask />);
  fireEvent.click(screen.getByRole("radio", { name: "Ask the agent live" }));
  // Suggestions: the question was asked before and has a saved answer.
  const suggestion = await screen.findByRole("button", { name: /asked 3 times/ });
  expect(screen.getByText("saved answer, no tokens")).toBeTruthy();
  fireEvent.click(suggestion); // fills the question box
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByText(/Saved answer from .*It used no model tokens/)).toBeTruthy();
  expect(posts[0]).toEqual({ question: trace.question, interactive: true });
  fireEvent.click(screen.getByRole("button", { name: "Ask again" }));
  await waitFor(() => expect(posts[1]).toEqual({ question: trace.question, interactive: true, fresh: true }));
  await waitFor(() => expect(screen.queryByText(/Saved answer from/)).toBeNull());
});

test("a replayed answer has Data and Chart tabs built from the computed numbers", async () => {
  render(<Ask params={{ trace: trace.id }} />);
  expect(await screen.findByRole("heading", { name: trace.question })).toBeTruthy();
  fireEvent.click(await screen.findByRole("tab", { name: /Data/ }));
  const answer = within(screen.getByRole("region", { name: "Answer" }));
  expect(answer.getByRole("heading", { name: "Revenue by store" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Download CSV" })).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Chart" }));
  // One breakdown by store: horizontal bars, largest first, one per store, readable without hovering.
  const bars = screen.getAllByLabelText(/^(Philadelphia|Brooklyn): /);
  expect(bars.map((b) => b.getAttribute("aria-label"))).toEqual(["Philadelphia: 425,467", "Brooklyn: 211,977"]);
  expect(screen.getByRole("combobox", { name: "Metric" })).toBeTruthy();
});

test("history lists your questions with their status in words and links to reopen them", async () => {
  const rows = [
    { question: "Which store had the higher income?", asked_at: 1791900000, status: "reused", tokens: 0, trace_id: "t-1" },
    { question: "How many orders?", asked_at: 1791800000, status: "failed", tokens: 1200, trace_id: null },
  ];
  globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => rows }));
  render(<History />);
  expect(await screen.findByText("Saved answer")).toBeTruthy();
  expect(screen.getByText("Failed")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Open" }).getAttribute("href")).toBe("#ask?trace=t-1");
  expect(screen.getByRole("link", { name: "Ask again" }).getAttribute("href")).toContain("q=How+many+orders");
  expect(screen.getByText(/used 1,200 model tokens/)).toBeTruthy();
});
