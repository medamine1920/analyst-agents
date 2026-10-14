/** Views live in the address hash, with optional parameters: #ask?q=..., #ask?trace=... */

export function parseHash(hash = window.location.hash) {
  const [view, query = ""] = hash.replace(/^#/, "").split("?");
  return { view, params: Object.fromEntries(new URLSearchParams(query)) };
}

export function linkTo(view, params = {}) {
  const query = new URLSearchParams(params).toString();
  return `#${view}${query ? `?${query}` : ""}`;
}
