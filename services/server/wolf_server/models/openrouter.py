"""OpenRouter adapter — a first-class, discoverable provider (ADR 0030).

OpenRouter speaks the OpenAI Chat Completions wire protocol, so all the request
translation, real SSE ``chat_stream``, tool-call accumulation, and 429/rate-limit
handling live in :class:`~wolf_server.models.openai.OpenAIAdapter`.  This thin
subclass exists so OpenRouter is a *named* provider rather than an opaque
``base_url`` override: it defaults the OpenRouter base URL + attribution headers
(``HTTP-Referer`` / ``X-Title``, which OpenRouter uses for app accounting) and
labels the descriptor ``provider="openrouter"``.

Selectable option only — local Ollama stays Wolf's default (free, uncapped,
on-prem).  OpenRouter ``:free`` models cost $0 but share a free-tier daily
request cap (a 429 surfaces as ``ModelProviderRateLimitError``).
"""

import httpx

from wolf_server.models.openai import OpenAIAdapter

OPENROUTER_BASE_URL = "https://openrouter.ai/api"


class OpenRouterAdapter(OpenAIAdapter):
    """OpenAI-compatible adapter pre-configured for OpenRouter."""

    def __init__(
        self,
        api_key: str,
        model_id: str,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        client: httpx.AsyncClient | None = None,
        referer: str = "https://github.com/wolf-soc/wolf",
        title: str = "Wolf",
    ) -> None:
        super().__init__(
            api_key=api_key,
            model_id=model_id,
            base_url=base_url,
            client=client,
            extra_headers={"HTTP-Referer": referer, "X-Title": title},
            provider="openrouter",
        )
