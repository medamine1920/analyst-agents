/** A tiny trend line for a headline number: plain SVG, so a dashboard of them stays light. */
export default function Sparkline({ values, label, width = 120, height = 32 }) {
  const shown = values.filter((v) => typeof v === "number");
  if (shown.length < 2) return null;
  const min = Math.min(...shown);
  const max = Math.max(...shown);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  // A gap where a period has no value (an average of nothing), rather than a line drawn through it.
  const segments = [];
  let current = [];
  values.forEach((v, i) => {
    if (typeof v !== "number") {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    current.push(`${(i * step).toFixed(1)},${(height - 3 - ((v - min) / span) * (height - 6)).toFixed(1)}`);
  });
  if (current.length) segments.push(current);
  const last = values.length - 1;
  const lastValue = values[last];
  return (
    <svg width={width} height={height} role="img" aria-label={label} className="overflow-visible">
      {segments.map((points) => (
        <polyline key={points[0]} points={points.join(" ")} fill="none" stroke="#2a78d6" strokeWidth="1.5" strokeLinejoin="round" />
      ))}
      {typeof lastValue === "number" && (
        <circle cx={last * step} cy={height - 3 - ((lastValue - min) / span) * (height - 6)} r="2.5" fill="#2a78d6" />
      )}
    </svg>
  );
}
