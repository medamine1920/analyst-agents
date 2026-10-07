const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Plain-language names for each step of the orchestrated graph. */
export const STEPS = {
  resolve: { title: "Check business terms", kind: "code" },
  plan: { title: "Plan the queries", kind: "model" },
  execute: { title: "Run the queries", kind: "code" },
  analyze: { title: "Compute the numbers", kind: "code" },
  analyze_partial: { title: "Compute the numbers from the queries that worked", kind: "code" },
  write: { title: "Write the answer", kind: "model" },
  review: { title: "Check the answer against the numbers", kind: "code" },
  give_up: { title: "Stop: no valid plan", kind: "code" },
};

export function formatNumber(value) {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

/** Cells: numbers with separators, timestamps as dates (months as "Mar 2025"). */
export function formatCell(column, value) {
  if (value === null || value === undefined) return "no value";
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return value ? "yes" : "no";
  const text = String(value);
  const date = text.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$/);
  if (date) {
    if (column.endsWith("__month")) return `${MONTHS[Number(date[2]) - 1]} ${date[1]}`;
    if (column.endsWith("__year")) return date[1];
    return `${date[1]}-${date[2]}-${date[3]}`;
  }
  return text;
}

export function percent(value) {
  return `${Math.round(value * 100)}%`;
}
