import os
import sys
from dataclasses import dataclass

import httpx

from app.core.config import settings


class LLMUnavailable(Exception):
    """Raised when the LLM gateway cannot be reached or rejects the request."""


@dataclass(frozen=True)
class ModelMessage:
    content: str
    reasoning_content: str | None = None
    model: str | None = None
    cost: str | None = None


# Groq's OpenAI-compatible chat completions endpoint.
# Confirmed live: https://api.groq.com/openai/v1/models returns 200 with GROQ_API_KEY.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _chat_endpoint() -> str:
    return f"{GROQ_BASE_URL}/chat/completions"


def running_under_pytest() -> bool:
    return "pytest" in sys.modules or os.environ.get("RECOUPE_TEST_MODE") == "1"


def default_model_name() -> str:
    if running_under_pytest():
        return settings.test_model_name or settings.model_name
    return settings.model_name


def chat(prompt: str, *, model: str | None = None, max_tokens: int | None = None) -> ModelMessage:
    api_key = settings.groq_api_key
    if not api_key:
        raise LLMUnavailable("GROQ_API_KEY is not configured")
    payload = {
        "model": model or default_model_name(),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens or settings.llm_max_tokens,
    }
    try:
        response = httpx.post(
            _chat_endpoint(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"gateway request failed: {exc}") from exc

    if response.status_code >= 400:
        raise LLMUnavailable(f"gateway returned HTTP {response.status_code}: {response.text[:300]}")

    try:
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
    except (ValueError, KeyError, IndexError) as exc:
        raise LLMUnavailable(f"gateway returned an unparsable response: {exc}") from exc

    return ModelMessage(
        content=message.get("content") or "",
        reasoning_content=message.get("reasoning_content"),
        model=data.get("model"),
        cost=str(data.get("cost", "")) or None,
    )


class LLMClient:
    def __init__(self, *, model: str | None = None, max_tokens: int | None = None):
        self.model = model or default_model_name()
        self.max_tokens = max_tokens

    def invoke(self, prompt: str) -> ModelMessage:
        return chat(prompt, model=self.model, max_tokens=self.max_tokens)


def get_llm() -> LLMClient:
    if not settings.llm_enabled:
        raise NotImplementedError("LLM inference is disabled; set LLM_ENABLED=true")
    return LLMClient()