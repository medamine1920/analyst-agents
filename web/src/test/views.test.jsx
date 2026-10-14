import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "../App";
import Ask from "../views/Ask";
import Admin from "../views/Admin";
import Connectors from "../views/Connectors";
import Evals from "../views/Evals";
import History from "../views/History";
import Sources from "../views/Sources";
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

test("an earlier run opens on the ask page itself: answer, check badge and every step", async () => {
  render(<Ask params={{ trace: trace.id }} />);
  expect(await screen.findByRole("heading", { name: trace.question })).toBeTruthy();
  expect(screen.queryByText(/Replay a saved run/)).toBeNull(); // one page: no separate replay mode
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
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Which store had the higher income?" } });
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
  expect(await screen.findByRole("link", { name: "+ New question" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "ana" })).toBeTruthy();
  expect(screen.queryByText(/tokens left today/)).toBeNull(); // the budget lives in Settings, not on every page
  expect(screen.queryByRole("link", { name: /Evaluations/ })).toBeNull(); // a development page, not in the menu
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
  // Nothing is suggested until the user starts typing.
  await new Promise((resolve) => setTimeout(resolve, 400));
  expect(screen.queryByText("Asked before")).toBeNull();
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Which st" } });
  // Then: the question was asked before and has a saved answer.
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

test("connectors: a new key is shown once, and the examples use it", async () => {
  let keys = [];
  globalThis.fetch = vi.fn(async (url, options) => {
    if (url === "/api/keys" && options?.method === "POST") {
      const created = { key: "ak_abcdefgh_secret", prefix: "ak_abcdefgh", name: JSON.parse(options.body).name };
      keys = [{ prefix: created.prefix, name: created.name, created_at: 1791900000, last_used_at: null }];
      return { ok: true, json: async () => created };
    }
    if (url === "/api/keys") return { ok: true, json: async () => keys };
    return { ok: false, status: 404, json: async () => ({}) };
  });
  render(<Connectors />);
  expect(await screen.findByText(/No keys yet/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("What the key is for"), { target: { value: "Sales sheet" } });
  fireEvent.click(screen.getByRole("button", { name: "Create key" }));
  expect(await screen.findByText(/it will not be shown again/)).toBeTruthy();
  expect(screen.getAllByText(/ak_abcdefgh_secret/).length).toBeGreaterThan(1); // the key box and the examples
  expect(await screen.findByText("Sales sheet")).toBeTruthy();
  expect(screen.getByText(/last used never/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "I have stored it" }));
  expect(screen.queryByText(/ak_abcdefgh_secret/)).toBeNull(); // gone from the page once dismissed
});

test("an invited user must choose a password before anything else, then sees the console", async () => {
  let mustChange = true;
  const posts = [];
  globalThis.fetch = vi.fn(async (url, options) => {
    if (options?.method === "POST") {
      posts.push([url, JSON.parse(options.body)]);
      mustChange = false;
      return { ok: true, json: async () => ({ changed: true }) };
    }
    if (url === "/api/auth/me") {
      return { ok: true, json: async () => ({ user: "bob", auth: true, role: "user", must_change_password: mustChange, usage: null }) };
    }
    const body = RESPONSES[url];
    return body ? { ok: true, json: async () => body } : { ok: false, status: 404, json: async () => ({}) };
  });
  render(<App />);
  expect(await screen.findByText(/The one you were given only works once/)).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Ask" })).toBeNull(); // nothing else until then
  fireEvent.change(screen.getByLabelText("Password you were given"), { target: { value: "temporary1" } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: "bob's own password" } });
  fireEvent.change(screen.getByLabelText("New password again"), { target: { value: "bob's own password" } });
  fireEvent.click(screen.getByRole("button", { name: "Change password" }));
  expect(await screen.findByRole("link", { name: "+ New question" })).toBeTruthy();
  expect(posts[0]).toEqual(["/api/account/password", { current: "temporary1", new: "bob's own password" }]);
});

test("admins invite people and see the one-time password once", async () => {
  let accounts = [{ name: "ana", role: "admin", must_change: false, created_at: 1, last_asked: null, tokens_today: 0 }];
  globalThis.fetch = vi.fn(async (url, options) => {
    if (options?.method === "POST" && url === "/api/admin/users") {
      accounts = [...accounts, { name: "sara", role: "user", must_change: true, created_at: 2, last_asked: null, tokens_today: 0 }];
      return { ok: true, json: async () => ({ name: "sara", role: "user", temporary_password: "Tmp-xyz-123" }) };
    }
    return { ok: true, json: async () => accounts };
  });
  render(<Admin session={{ user: "ana", role: "admin" }} />);
  expect(await screen.findByText("(you)")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull(); // no removing yourself
  fireEvent.change(screen.getByPlaceholderText(/sign-in name/), { target: { value: "sara" } });
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  expect(await screen.findByText("Tmp-xyz-123")).toBeTruthy();
  expect(await screen.findByText("has not chosen a password yet")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(screen.queryByText("Tmp-xyz-123")).toBeNull();
});

test("data sources: a CSV upload shows as building, then ready with its tables and metrics", async () => {
  let status = "building";
  const uploads = [];
  globalThis.fetch = vi.fn(async (url, options) => {
    if (url === "/api/sources/csv") {
      uploads.push(options.body);
      return { ok: true, json: async () => ({ id: "abc", status: "building" }) };
    }
    const mine = { id: "abc", name: "Sales 2025", kind: "csv", status, error: null,
      ...(status === "ready" ? { tables: [{ label: "Sales 2025", rows: 5 }], metrics: 8, notes: [] } : {}) };
    status = "ready"; // the next poll finds it built
    return { ok: true, json: async () => [{ id: "sample", name: "Sample coffee shop", kind: "sample", status: "ready" }, mine] };
  });
  render(<Sources />);
  expect(await screen.findByText(/Building: reading the tables/)).toBeTruthy();
  const file = new File(["a,b\n1,2\n"], "Sales 2025.csv", { type: "text/csv" });
  fireEvent.change(screen.getByLabelText("CSV files"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Add data source" }));
  await waitFor(() => expect(uploads.length).toBe(1));
  expect(uploads[0].get("files").name).toBe("Sales 2025.csv");
  expect(await screen.findByText(/5 rows/, {}, { timeout: 4000 })).toBeTruthy();
  expect(screen.getByText(/8 metrics/)).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Connect a live database" }));
  expect(screen.getByText(/never written to/)).toBeTruthy();
});

test("settings: people and developer pages are for admins; two-step sign-in shows a key to scan once", async () => {
  const { default: Settings } = await import("../views/Settings");
  globalThis.fetch = vi.fn(async (url, options) => {
    if (url === "/api/account/2fa/start") {
      return { ok: true, json: async () => ({ secret: "JBSWY3DPEHPK3PXP", uri: "otpauth://totp/Analyst%20Console:ana?secret=JBSWY3DPEHPK3PXP" }) };
    }
    return { ok: true, json: async () => [] };
  });
  const user = { user: "bob", role: "user", two_factor: false, usage: { tokens_left: 1, daily_tokens: 2, questions_last_hour: 0, questions_per_hour: 30 } };
  const { unmount } = render(<Settings session={user} params={{ tab: "security" }} />);
  expect(screen.queryByRole("link", { name: /People/ })).toBeNull();
  expect(screen.queryByRole("link", { name: /Developer/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Turn on two-step sign-in" }));
  expect(await screen.findByText("JBSWY3DPEHPK3PXP")).toBeTruthy();
  expect(await screen.findByAltText(/QR code/)).toBeTruthy();
  unmount();
  render(<Settings session={{ ...user, role: "admin" }} params={{ tab: "usage" }} />);
  expect(screen.getByRole("link", { name: /People/ })).toBeTruthy();
  expect(screen.getByRole("link", { name: /Developer/ })).toBeTruthy();
  expect(screen.getByText(/questions in the last hour/)).toBeTruthy();
});

test("the sidebar creates a project and lists recent questions", async () => {
  const { default: Sidebar } = await import("../components/Sidebar");
  let projects = [];
  const recent = [{ question: "Which store had the higher income?", asked_at: 1, trace_id: "t-1" }];
  globalThis.fetch = vi.fn(async (url, options) => {
    if (url === "/api/projects" && options?.method === "POST") {
      projects = [{ id: "abcdef1234", name: JSON.parse(options.body).name, questions: 0 }];
      return { ok: true, json: async () => projects[0] };
    }
    if (url === "/api/projects") return { ok: true, json: async () => projects };
    return { ok: true, json: async () => recent };
  });
  render(<Sidebar route={{ view: "ask", params: {} }} session={{ auth: true, user: "ana" }} onSignOut={() => {}} />);
  expect(await screen.findByRole("link", { name: "Which store had the higher income?" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "New project" }));
  fireEvent.change(screen.getByLabelText("New project name"), { target: { value: "Q3 review" } });
  fireEvent.submit(screen.getByLabelText("New project name").closest("form"));
  expect(await screen.findByRole("link", { name: /Q3 review/ })).toBeTruthy();
  expect(window.location.hash).toBe("#ask?project=abcdef1234");
});

test("connections: tokens are typed into hidden fields, then only a masked hint is listed", async () => {
  const { default: Connections } = await import("../views/Connections");
  const catalog = {
    kinds: [
      { kind: "link", label: "File from a link", description: "", fields: [], imports: true, exports: false },
      { kind: "airtable", label: "Airtable", description: "Import the tables of an Airtable base.", imports: true, exports: false,
        fields: [{ key: "base_id", label: "Base id", secret: false, required: true }, { key: "token", label: "Personal access token", secret: true, required: true }] },
      { kind: "google", label: "Google Sheets & Drive", description: "", fields: [], imports: true, exports: true, oauth: true, available: false },
    ],
    not_possible: { linkedin: "LinkedIn does not offer a data API to apps like this one." },
  };
  let connections = [];
  const posts = [];
  globalThis.fetch = vi.fn(async (url, options) => {
    if (url === "/api/connectors/catalog") return { ok: true, json: async () => catalog };
    if (url === "/api/connectors" && options?.method === "POST") {
      posts.push(JSON.parse(options.body));
      connections = [{ id: "c1", kind: "airtable", name: "CRM", hint: "token: patS…90", created_at: 1, last_used_at: null }];
      return { ok: true, json: async () => connections[0] };
    }
    return { ok: true, json: async () => connections };
  });
  render(<Connections />);
  expect(await screen.findByText(/LinkedIn does not offer a data API/)).toBeTruthy();
  const card = screen.getByText("Import the tables of an Airtable base.").closest("li");
  fireEvent.click(within(card).getByRole("button", { name: "Connect" }));
  const token = within(card).getByLabelText(/Personal access token/);
  expect(token.getAttribute("type")).toBe("password");
  fireEvent.change(within(card).getByLabelText(/Base id/), { target: { value: "app1" } });
  fireEvent.change(token, { target: { value: "patSECRET1234567890" } });
  fireEvent.click(within(card).getByRole("button", { name: "Connect" }));
  expect(await screen.findByText(/token: patS…90/)).toBeTruthy();
  expect(posts[0]).toEqual({ kind: "airtable", name: "Airtable", values: { base_id: "app1", token: "patSECRET1234567890" } });
  expect(document.body.textContent).not.toContain("patSECRET1234567890");
  const googleCard = screen.getByText("Google Sheets & Drive", { selector: "p" }).closest("li");
  fireEvent.click(within(googleCard).getByRole("button", { name: "Connect" }));
  expect(within(googleCard).getByText(/Not set up on this console yet/)).toBeTruthy();
  expect(within(googleCard).getByRole("button", { name: "Sign in with Google" }).disabled).toBe(true);
});
