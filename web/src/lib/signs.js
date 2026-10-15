/**
 * Colouring numbers by sign: gains in green, losses in red (or blue and orange, colour-blind safe), adjustable
 * under Settings > Display. A per-browser preference: it only changes how numbers look.
 *
 * Two readings of "good": by sign (above zero is green), or by meaning, where a rise in something the business
 * wants lower (costs, returns, churn) shows red. The meaning comes from the sales expert's definitions.
 */
import { createContext, useContext, useEffect, useState } from "react";

const KEY = "analyst.signs";
export const DEFAULTS = { palette: "red-green", basis: "meaning" }; // palette: red-green | blue-orange | off
const EVENT = "analyst:signs-changed";

export function readSigns() {
  try {
    return { ...DEFAULTS, ...JSON.parse(window.localStorage.getItem(KEY) || "{}") };
  } catch {
    return DEFAULTS; // storage blocked: the defaults still work
  }
}

export function saveSigns(next) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // not remembered, but applied to this page
  }
  applyPalette(next);
  window.dispatchEvent(new CustomEvent(EVENT, { detail: next }));
}

/** The palette is CSS variables switched on <html>, so every coloured number follows at once. */
export function applyPalette(signs = readSigns()) {
  document.documentElement.dataset.signs = signs.palette;
}

export function useSigns() {
  const [signs, setSigns] = useState(readSigns);
  useEffect(() => {
    const changed = (e) => setSigns(e.detail);
    window.addEventListener(EVENT, changed);
    return () => window.removeEventListener(EVENT, changed);
  }, []);
  return signs;
}

// Columns whose sign is the point: a change, a gap, a profit. Elsewhere only negative numbers are coloured.
const SIGNED = /(^|_)(change|delta|diff|difference|growth|contribution|variance|profit)(_|$)|change_pct$/;

/** Metric name -> whether higher is better (from the sales expert), for the "meaning" reading. */
export const PolarityContext = createContext({});

/**
 * The Tailwind class for a number: "text-gain", "text-loss" or "". `metric` is the metric the number is about,
 * when known, so a rise in costs reads as a loss.
 */
export function tone(signs, polarity, column, value, metric = null) {
  if (signs.palette === "off" || typeof value !== "number" || value === 0 || Number.isNaN(value)) return "";
  const signed = SIGNED.test(column ?? "");
  if (!signed && value > 0) return "";
  const better = signs.basis === "meaning" ? polarity[metric ?? column] ?? polarityOf(polarity, column) : true;
  const good = better === false ? value < 0 : value > 0;
  return good ? "text-gain" : "text-loss";
}

/** "cost_total_change" -> the polarity of the metric it starts with, if known. */
function polarityOf(polarity, column = "") {
  const metric = Object.keys(polarity).find((name) => column.startsWith(name));
  return metric ? polarity[metric] : true;
}

/** The current gain and loss colours as values, for charts (which cannot use CSS classes). */
export function signColors() {
  const style = getComputedStyle(document.documentElement);
  const read = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
  return readSigns().palette === "off"
    ? { gain: "#52514e", loss: "#898781" }
    : { gain: read("--color-gain", "#15803d"), loss: read("--color-loss", "#b91c1c") };
}

export function useTone() {
  const signs = useSigns();
  const polarity = useContext(PolarityContext);
  return (column, value, metric) => tone(signs, polarity, column, value, metric);
}
