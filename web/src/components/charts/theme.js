/** Chart tokens shared by every chart, kept apart from ECharts so pages load it only when they draw. */

// The validated categorical order and the quiet chrome (see the dataviz rules in CLAUDE.md).
export const THEME = {
  series: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
  ink: "#1a2233",
  ink2: "#52514e",
  muted: "#898781",
  grid: "#e1e0d9",
  axis: "#c3c2b7",
  surface: "#ffffff",
  font: '"IBM Plex Sans", system-ui, sans-serif',
};

export const baseOption = {
  color: THEME.series,
  textStyle: { fontFamily: THEME.font, color: THEME.ink2 },
  animationDuration: 300,
  aria: { enabled: true },
  tooltip: {
    backgroundColor: THEME.surface,
    borderColor: "rgba(11,11,11,0.10)",
    textStyle: { color: THEME.ink, fontFamily: THEME.font, fontSize: 13 },
    extraCssText: "box-shadow: 0 2px 8px rgba(0,0,0,0.08); border-radius: 8px;",
  },
  toolbox: {
    right: 0,
    top: 0,
    itemSize: 14,
    iconStyle: { borderColor: THEME.muted },
    feature: { saveAsImage: { title: "Save as image", pixelRatio: 2, backgroundColor: THEME.surface } },
  },
};

