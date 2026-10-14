/** Tell the app the session ended (expired, or signed out elsewhere), so it shows the sign-in screen. */
function signedOut(response) {
  if (response.status === 401) window.dispatchEvent(new Event("analyst:signed-out"));
}

/** The API's own explanation ("Today's budget ... is used up"), or a generic one. */
async function reason(response, fallback) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // not JSON: use the fallback
  }
  return fallback;
}

export async function getJSON(path) {
  const response = await fetch(path);
  if (!response.ok) {
    signedOut(response);
    throw new Error(await reason(response, `The API returned ${response.status} for ${path}. Is it running on port 8001?`));
  }
  return response.json();
}

export async function postJSON(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) {
    if (path !== "/api/auth/login") signedOut(response);
    throw new Error(await reason(response, `The API refused the request (${response.status}).`));
  }
  return response.json();
}

/** POST a question and call onEvent for every streamed step (server-sent events). */
export function streamAsk(question, interactive, onEvent, signal, fresh = false) {
  return streamPost("/api/ask", { question, interactive, ...(fresh ? { fresh: true } : {}) }, onEvent, signal);
}

/** Continue a run that paused to ask which definition was meant. */
export function streamResume(threadId, choice, onEvent, signal) {
  return streamPost("/api/resume", { thread_id: threadId, choice }, onEvent, signal);
}

async function streamPost(path, body, onEvent, signal) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    signedOut(response);
    throw new Error(await reason(response, `The API refused the request (${response.status}).`));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }
}
