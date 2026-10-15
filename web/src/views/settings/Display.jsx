import { formatNumber } from "../../lib/format";
import { PolarityContext, saveSigns, useSigns, useTone } from "../../lib/signs";

const PALETTES = [
  ["red-green", "Green and red", "Gains in green, losses in red."],
  ["blue-orange", "Blue and orange", "Easier to tell apart with red-green colour blindness."],
  ["off", "No colour", "Numbers stay in the text colour; signs and arrows still show the direction."],
];
const BASES = [
  ["meaning", "Good or bad for the business", "A rise in costs, returns or churn shows red, even though it is above zero."],
  ["sign", "Above or below zero", "Every positive change is green and every negative one red, whatever it measures."],
];
const SAMPLE = [
  ["Revenue", "revenue_change", 1240.5, "revenue"],
  ["Revenue", "revenue_change", -310, "revenue"],
  ["Costs", "cost_change", 420, "cost"],
  ["Return rate", "return_rate_change_pct", -1.8, "return_rate"],
];

function Choice({ name, value, options, onChange }) {
  return (
    <fieldset className="space-y-2">
      {options.map(([id, label, help]) => (
        <label key={id} className="flex cursor-pointer gap-3 rounded-lg border border-rule bg-surface p-3 has-[:checked]:border-ink">
          <input type="radio" name={name} value={id} checked={value === id} onChange={() => onChange(id)} className="mt-1" />
          <span>
            <span className="block font-medium">{label}</span>
            <span className="block text-sm text-muted">{help}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function Preview() {
  const tone = useTone();
  return (
    <table className="w-full max-w-sm text-sm">
      <caption className="pb-2 text-left text-muted">Preview: change against the month before</caption>
      <tbody>
        {SAMPLE.map(([label, column, value, metric]) => (
          <tr key={`${label}${value}`} className="border-t border-rule">
            <td className="py-1.5">{label}</td>
            <td className={`py-1.5 text-right font-medium ${tone(column, value, metric)}`}>
              {value > 0 ? "▲ +" : "▼ "}
              {formatNumber(value)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** How numbers look: colours for gains and losses, and what counts as a gain. Kept in this browser. */
export default function Display() {
  const signs = useSigns();
  const set = (key) => (value) => saveSigns({ ...signs, [key]: value });
  return (
    <div className="grid max-w-4xl grid-cols-1 gap-8 lg:grid-cols-2">
      <div className="space-y-6">
        <section className="space-y-2">
          <h3 className="font-semibold">Colours for gains and losses</h3>
          <Choice name="palette" value={signs.palette} options={PALETTES} onChange={set("palette")} />
        </section>
        {signs.palette !== "off" && (
          <section className="space-y-2">
            <h3 className="font-semibold">What counts as a gain</h3>
            <Choice name="basis" value={signs.basis} options={BASES} onChange={set("basis")} />
          </section>
        )}
      </div>
      <section className="space-y-2 self-start rounded-xl border border-rule bg-surface p-5">
        <PolarityContext.Provider value={{ revenue: true, cost: false, return_rate: false }}>
          <Preview />
        </PolarityContext.Provider>
        <p className="text-xs text-muted">
          Applies to tables, changes on the dashboard and waterfall charts. Kept in this browser only.
        </p>
      </section>
    </div>
  );
}
