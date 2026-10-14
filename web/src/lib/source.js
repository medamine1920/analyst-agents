/** Which data source the user is asking about: remembered in this browser, "sample" by default. */

const KEY = "analyst.source";
export const SAMPLE = "sample";

export function currentSource() {
  try {
    return window.localStorage.getItem(KEY) || SAMPLE;
  } catch {
    return SAMPLE; // storage can be blocked (private windows): the sample shop still works
  }
}

export function chooseSource(id) {
  try {
    window.localStorage.setItem(KEY, id);
  } catch {
    // not remembered, but still used for this page
  }
  window.dispatchEvent(new CustomEvent("analyst:source-changed", { detail: id }));
}
