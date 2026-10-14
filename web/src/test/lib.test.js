import { expect, test } from "vitest";

import { toCsv } from "../lib/csv";
import { chartPoints, datasets } from "../lib/results";
import { niceTicks } from "../lib/scale";

const step = (node, data) => ({ type: "step", node, data });

function run(planSteps, factSteps) {
  return [step("plan", { plan: { steps: planSteps } }), step("analyze", { facts: { steps: factSteps } })];
}

test("each query gets the chart that fits its breakdown", () => {
  const steps = run(
    [
      { id: "total", metrics: ["order_count"], group_by: [] },
      { id: "monthly", metrics: ["order_count"], group_by: ["metric_time__month"] },
      { id: "by_store", metrics: ["order_count"], group_by: ["store__store_name"] },
      { id: "month_store", metrics: ["order_count"], group_by: ["metric_time__month", "store__store_name"] },
      { id: "by_product", metrics: ["order_count"], group_by: ["product__product_name", "store__store_name"] },
    ],
    [
      { id: "total", rows: [{ order_count: 61948 }] },
      { id: "monthly", rows: [{ metric_time__month: "2025-02-01", order_count: 3446 }, { metric_time__month: "2025-01-01", order_count: 3000 }] },
      { id: "by_store", rows: [{ store__store_name: "Brooklyn", order_count: 22017 }, { store__store_name: "Philadelphia", order_count: 39931 }] },
      { id: "month_store", rows: [{ metric_time__month: "2025-03-01", store__store_name: "Brooklyn", order_count: 2000 }] },
      { id: "by_product", rows: [{ product__product_name: "Tea", store__store_name: "Brooklyn", order_count: 1 }] },
      { id: "empty", rows: [] },
    ],
  );
  const kinds = Object.fromEntries(datasets(steps).map((d) => [d.id, d.chart]));
  expect(kinds).toEqual({ total: "tiles", monthly: "line", by_store: "bar", month_store: "lines", by_product: null });
  const [, monthly, byStore] = datasets(steps);
  expect(chartPoints(monthly, "order_count").map((p) => p.x)).toEqual(["2025-01-01", "2025-02-01"]); // in time order
  expect(chartPoints(byStore, "order_count").map((p) => p.x)).toEqual(["Philadelphia", "Brooklyn"]); // largest first
});

test("computed columns stay in the table but are not offered as chart metrics", () => {
  const steps = run(
    [{ id: "s", metrics: ["revenue_pre_tax"], group_by: ["store__store_name"] }],
    [{ id: "s", rows: [{ store__store_name: "Brooklyn", revenue_pre_tax: 1, revenue_pre_tax_share_pct: 100 }] }],
  );
  expect(datasets(steps)[0].metrics).toEqual(["revenue_pre_tax"]);
});

test("csv cells are quoted when needed and never run as spreadsheet formulas", () => {
  const csv = toCsv([{ name: 'He said "hi", twice', value: 3 }, { name: "=HYPERLINK(1)", value: -2 }]);
  expect(csv.split(String.fromCharCode(13, 10))).toEqual(["name,value", '"He said ""hi"", twice",3', "'=HYPERLINK(1),-2"]);
});

test("axis ticks are round numbers that cover the data", () => {
  expect(niceTicks(425467)).toEqual([0, 200000, 400000, 600000]);
  expect(niceTicks(9.63)).toEqual([0, 2.5, 5, 7.5, 10]);
});
