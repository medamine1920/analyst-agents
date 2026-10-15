import { formatCell } from "../lib/format";
import { useTone } from "../lib/signs";

/** A governed identifier (metric or dimension name): the only place monospace is used. */
export function Ident({ children }) {
  return (
    <code className="rounded bg-paper px-1.5 py-0.5 font-mono text-[0.8rem] text-ink">{children}</code>
  );
}

const KINDS = {
  model: { text: "Model", style: "bg-model-tint text-model" },
  code: { text: "Code", style: "bg-code-tint text-code" },
  person: { text: "You", style: "bg-paper text-ink" },
};

export function KindTag({ kind }) {
  const { text, style } = KINDS[kind] ?? KINDS.code;
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${style}`}>{text}</span>;
}

export function DataTable({ rows, limit = 12 }) {
  if (!rows || rows.length === 0) return <p className="text-sm text-muted">No rows returned.</p>;
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  const shown = rows.slice(0, limit);
  const tone = useTone();
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} className="border-b border-rule px-2 py-1.5 text-left font-mono text-xs font-medium text-muted">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => {
                const value = row[column];
                const numeric = typeof value === "number";
                return (
                  <td
                    key={column}
                    className={`border-b border-rule/60 px-2 py-1.5 ${numeric ? "text-right" : ""} ${value == null ? "text-muted italic" : ""} ${tone(column, value, row.metric)}`}
                  >
                    {formatCell(column, value)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > limit && <p className="mt-1 text-xs text-muted">{rows.length - limit} more rows not shown.</p>}
    </div>
  );
}
