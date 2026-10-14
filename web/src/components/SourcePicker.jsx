import { useEffect, useState } from "react";

import { getJSON } from "../lib/api";
import { chooseSource, currentSource, SAMPLE } from "../lib/source";
import { linkTo } from "../lib/route";

/** Pick the data source to ask about. Sources still building or failed are listed but cannot be picked. */
export function useSource() {
  const [source, setSource] = useState(currentSource);
  useEffect(() => {
    const onChange = (event) => setSource(event.detail);
    window.addEventListener("analyst:source-changed", onChange);
    return () => window.removeEventListener("analyst:source-changed", onChange);
  }, []);
  return source;
}

/** compact: a small inline control for the question box's options row. */
export default function SourcePicker({ compact = false }) {
  const source = useSource();
  const [sources, setSources] = useState([]);
  useEffect(() => {
    getJSON("/api/sources")
      .then((list) => {
        setSources(list ?? []);
        // A remembered source that was removed (or is not ready) falls back to the sample shop.
        if (!list?.some((s) => s.id === source && s.status === "ready")) chooseSource(SAMPLE);
      })
      .catch(() => setSources([]));
    // only on first render: the list does not change while the picker is on screen
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (sources.length <= 1) {
    return (
      <p className="text-sm text-muted">
        {compact ? "Data: sample coffee shop · " : "Asking the sample coffee shop. "}
        <a className="underline" href={linkTo("sources")}>{compact ? "add yours" : "Add your own data"}</a>
      </p>
    );
  }
  return (
    <label className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted">Data</span>
      <select
        value={source}
        onChange={(e) => chooseSource(e.target.value)}
        className={`rounded-lg border border-rule bg-surface ${compact ? "px-2 py-1" : "px-2.5 py-1.5"}`}
      >
        {sources.map((s) => (
          <option key={s.id} value={s.id} disabled={s.status !== "ready"}>
            {s.name}
            {s.status === "building" ? " (building…)" : s.status === "failed" ? " (failed)" : ""}
          </option>
        ))}
      </select>
      {!compact && (
        <a className="text-muted underline hover:text-ink" href={linkTo("sources")}>
          Manage
        </a>
      )}
    </label>
  );
}
