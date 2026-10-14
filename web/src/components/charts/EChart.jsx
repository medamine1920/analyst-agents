import { BarChart as Bars, LineChart as Lines } from "echarts/charts";
import { AriaComponent, DataZoomComponent, GridComponent, LegendComponent, ToolboxComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

// Only what the console draws, so the bundle stays small.
echarts.use([Bars, Lines, GridComponent, TooltipComponent, LegendComponent, ToolboxComponent, DataZoomComponent, AriaComponent, CanvasRenderer]);

/** Whether this browser can draw on a canvas at all (test environments and some locked-down ones cannot). */
function canDraw() {
  try {
    return Boolean(document.createElement("canvas").getContext("2d"));
  } catch {
    return false;
  }
}

/** An ECharts canvas that follows its container's width and is cleaned up when it leaves the page. */
export default function EChart({ option, height, label }) {
  const holder = useRef(null);
  const chart = useRef(null);

  useEffect(() => {
    if (!holder.current || !canDraw()) return undefined; // the value list below still carries every number
    try {
      chart.current = echarts.init(holder.current, null, { renderer: "canvas" });
    } catch {
      return undefined; // no canvas (tests, very old browsers): the value list below still carries every number
    }
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => chart.current?.resize());
    resize?.observe(holder.current);
    return () => {
      resize?.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    try {
      chart.current?.setOption(option, true);
    } catch {
      // a chart that cannot draw leaves the value list and the Data tab
    }
  }, [option]);

  return <div ref={holder} role="img" aria-label={label} style={{ height, width: "100%" }} />;
}
