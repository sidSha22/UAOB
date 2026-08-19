"""OpenAI-compatible chat-completion adapter. Talks to whatever server is
configured via LLM_BASE_URL / LLM_MODEL -- a local llama.cpp server by
default. Swapping to a hosted provider (OpenAI, etc.) is an environment
variable change only (LLM_BASE_URL, LLM_MODEL, optionally LLM_API_KEY),
no code changes. Fails loudly if the server is unreachable or errors --
never falls back silently."""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_TIMEOUT = 180
DEFAULT_MAX_TOKENS = 1536


def _config():
    base_url = os.environ.get("LLM_BASE_URL")
    model = os.environ.get("LLM_MODEL")
    if not base_url or not model:
        raise RuntimeError(
            "Missing LLM_BASE_URL or LLM_MODEL environment variable(s). "
            "Set them in the environment or in a .env file (see .env.example)."
        )
    return base_url.rstrip("/"), model


def chat(messages, temperature=0.0, max_tokens=DEFAULT_MAX_TOKENS):
    """Send a chat-completion request. Returns the assistant's text
    content. Raises RuntimeError with a clear message if the LLM server is
    unreachable or returns an error -- never falls back silently."""
    base_url, model = _config()
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("LLM_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        resp = requests.post(
            f"{base_url}/chat/completions", json=payload, headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            f"Could not reach LLM server at {base_url} (model={model}): {exc}. "
            f"Is the server running? (LLM_BASE_URL/LLM_MODEL are read from the environment.)"
        ) from exc

    if resp.status_code != 200:
        raise RuntimeError(
            f"LLM server at {base_url} returned HTTP {resp.status_code}: {resp.text[:500]}"
        )

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected LLM response shape from {base_url}: {data}") from exc
