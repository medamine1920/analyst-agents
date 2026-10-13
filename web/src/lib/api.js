export async function getJSON(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`The API returned ${response.status} for ${path}. Is it running on port 8001?`);
  }
  return response.json();
}

/** POST a question and call onEvent for every streamed step (server-sent events). */
export function streamAsk(question, interactive, onEvent, signal) {
  return streamPost("/api/ask", { question, interactive }, onEvent, signal);
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
    const detail = await response.text();
    throw new Error(`The API refused the request (${response.status}): ${detail.slice(0, 200)}`);
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
