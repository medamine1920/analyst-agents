"""Model providers a user can bring their own key for: Claude, GPT, Gemini and Groq.

A key is checked by listing the provider's models (through the console's safe HTTP layer), so a wrong
key is refused when it is added, not in the middle of a question. The planner and writer are then
built from LangChain's client for that provider; the rest of the agent does not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.connectors import http


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    default: str                      # used when the user does not pick a model
    models_url: str
    help: str                         # where to create a key
    # Ways to ask for the plan, best first; the next one is tried if the provider refuses the current one.
    plan_modes: tuple[str, ...] = ("json_schema", "json_mode")
    skip: tuple[str, ...] = field(default=())  # model ids containing these are not chat models


PROVIDERS: dict[str, Provider] = {p.id: p for p in (
    Provider("anthropic", "Claude (Anthropic)", "claude-opus-5-5", "https://api.anthropic.com/v1/models?limit=100",
             "console.anthropic.com → API keys",
             # Current Claude models refuse forced tool calls, so structured output comes first.
             plan_modes=("json_schema", "function_calling")),
    Provider("openai", "GPT (OpenAI)", "gpt-5", "https://api.openai.com/v1/models",
             "platform.openai.com → API keys",
             skip=("audio", "realtime", "tts", "transcribe", "image", "embedding", "search", "moderation",
                   "whisper", "dall-e", "davinci", "babbage", "codex")),
    Provider("google", "Gemini (Google)", "gemini-2.5-pro",
             "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
             "aistudio.google.com → Get API key",
             skip=("embedding", "aqa", "imagen", "veo", "tts", "image")),
    Provider("groq", "Groq", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1/models",
             "console.groq.com → API keys",
             skip=("whisper", "tts", "guard", "playai", "orpheus")),
)}


def headers(provider: str, key: str) -> dict[str, str]:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if provider == "google":
        return {"x-goog-api-key": key}  # in a header, never in the address
    return {"Authorization": f"Bearer {key}"}


async def check_key(provider: str, key: str) -> list[str]:
    """The chat models this key can use, newest names first. Raises http.ConnectorError if the key is refused."""
    spec = PROVIDERS[provider]
    response = await http.request("GET", spec.models_url, headers=headers(provider, key), max_bytes=5_000_000)
    body = response.json()
    if provider == "google":
        ids = [m["name"].removeprefix("models/") for m in body.get("models", [])
               if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m["name"]]
    else:
        ids = [m["id"] for m in body.get("data", [])]
    chat = [i for i in ids if not any(word in i.lower() for word in spec.skip)]
    if provider == "openai":
        chat = [i for i in chat if i.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))]
    return sorted(set(chat), reverse=True)


def chat_model(provider: str, model: str, key: str):
    """LangChain's client for this provider, with the user's key."""
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # No temperature: current Claude models refuse sampling settings.
        return ChatAnthropic(model=model, api_key=key, max_tokens=16000, max_retries=3)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=key, max_retries=3)  # reasoning models refuse temperature=0
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, google_api_key=key, temperature=0, max_retries=3)
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=model, api_key=key, temperature=0, max_retries=5)
    raise ValueError(f"Unknown provider: {provider}")


def model_fns(provider: str, model: str, key: str):
    """The planner and writer for a user's own key."""
    from app.agents.orchestrated.graph import make_plan_fn, make_write_fn

    client = chat_model(provider, model, key)
    return make_plan_fn(client, PROVIDERS[provider].plan_modes), make_write_fn(client)
