"""OpenRouter / OpenAI-compatible adapter — streaming, tool-calls, errors (ADR 0030).

The OpenRouter path rides the OpenAI Chat Completions wire protocol. These tests
pin the load-bearing behaviour with an httpx MockTransport (no live API):
  - chat_stream yields content deltas + a final ChatStreamDone with full content;
  - streamed tool-calls (arguments arrive as fragmented JSON keyed by index) are
    accumulated into a complete ToolCall;
  - a 429 surfaces as ModelProviderRateLimitError (the daily free-tier cap);
  - OpenRouterAdapter pins the OpenRouter base + attribution headers + provider;
  - the model_resolver factory builds an OpenRouterAdapter for provider 'openrouter'.
"""

import json
from typing import Any

import httpx
import pytest
from wolf_schema import ChatRequest
from wolf_schema.chat import Message, MessageRole
from wolf_server.config import get_settings
from wolf_server.models.interface import ChatStreamDelta, ChatStreamDone
from wolf_server.models.openai import (
    ModelProviderRateLimitError,
    OpenAIAdapter,
)
from wolf_server.models.openrouter import OPENROUTER_BASE_URL, OpenRouterAdapter

_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"  # KNOWN_MODELS → native tools full


def _sse(*chunks: dict[str, Any]) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    body += "data: [DONE]\n\n"
    return body.encode("utf-8")


def _adapter(transport: httpx.MockTransport, *, model: str = _MODEL) -> OpenRouterAdapter:
    client = httpx.AsyncClient(base_url=OPENROUTER_BASE_URL, transport=transport, timeout=5.0)
    return OpenRouterAdapter(api_key="test-key", model_id=model, client=client)


def _req(text: str) -> ChatRequest:
    return ChatRequest(
        messages=[Message(role=MessageRole.user, content=text)], max_tokens=64, temperature=0.0
    )


@pytest.mark.asyncio
async def test_chat_stream_yields_content_deltas_then_done() -> None:
    sse = _sse(
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}},
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        # base (…/api) + /v1/chat/completions = the correct OpenRouter endpoint.
        assert request.url.path == "/api/v1/chat/completions"
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    adapter = _adapter(httpx.MockTransport(_handler))
    deltas: list[str] = []
    done = None
    async for ev in adapter.chat_stream(_req("hi")):
        if isinstance(ev, ChatStreamDelta):
            deltas.append(ev.content_delta)
        elif isinstance(ev, ChatStreamDone):
            done = ev.response
    assert deltas == ["Hel", "lo"]
    assert done is not None
    assert done.content == "Hello"
    assert done.stop_reason == "stop"
    assert done.input_tokens == 3
    assert done.output_tokens == 2


@pytest.mark.asyncio
async def test_chat_stream_accumulates_fragmented_tool_calls() -> None:
    # arguments arrive as concatenated JSON fragments keyed by index (OpenAI SSE).
    sse = _sse(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "get_weather", "arguments": "{\"ci"}}
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "ty\": \"Paris\"}"}}
        ]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    )

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    adapter = _adapter(httpx.MockTransport(_handler))
    done = None
    async for ev in adapter.chat_stream(_req("weather?")):
        if isinstance(ev, ChatStreamDone):
            done = ev.response
    assert done is not None
    assert len(done.tool_calls) == 1
    tc = done.tool_calls[0]
    assert tc.name == "get_weather"
    assert tc.arguments == {"city": "Paris"}
    assert tc.id == "call_1"
    assert done.stop_reason == "tool_calls"


@pytest.mark.asyncio
async def test_chat_stream_429_raises_rate_limit_error() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="free-tier daily limit reached")

    adapter = _adapter(httpx.MockTransport(_handler))
    with pytest.raises(ModelProviderRateLimitError, match="rate limit"):
        async for _ev in adapter.chat_stream(_req("hi")):
            pass


@pytest.mark.asyncio
async def test_chat_blocking_429_raises_rate_limit_error() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="capped")

    adapter = _adapter(httpx.MockTransport(_handler))
    with pytest.raises(ModelProviderRateLimitError):
        await adapter.chat(_req("hi"))


@pytest.mark.asyncio
async def test_chat_blocking_parses_tool_call() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_9",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"city": "Berlin"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 4},
            },
        )

    adapter = _adapter(httpx.MockTransport(_handler))
    resp = await adapter.chat(_req("weather in Berlin?"))
    assert resp.tool_calls[0].name == "get_weather"
    assert resp.tool_calls[0].arguments == {"city": "Berlin"}


def test_openrouter_adapter_pins_base_headers_and_provider() -> None:
    adapter = OpenRouterAdapter(api_key="k", model_id="openrouter/owl-alpha")
    assert adapter._base_url == OPENROUTER_BASE_URL
    assert adapter.capability().provider == "openrouter"
    assert adapter.capability().model_id == "openrouter/owl-alpha"
    # Attribution headers OpenRouter uses for app accounting.
    headers = adapter._client.headers
    assert headers.get("HTTP-Referer")
    assert headers.get("X-Title") == "Wolf"


@pytest.mark.asyncio
async def test_factory_builds_openrouter_adapter() -> None:
    from wolf_server.agent.model_resolver import _build_provider

    class _FakeSecrets:
        async def get(self, key: str) -> str | None:
            return "secret-key" if key == "model.openrouter.api_key" else None

    provider = await _build_provider(
        provider_name="openrouter",
        model_id=_MODEL,
        api_key_ref="model.openrouter.api_key",
        settings=get_settings(),
        secrets=_FakeSecrets(),  # type: ignore[arg-type]
    )
    assert isinstance(provider, OpenRouterAdapter)
    assert provider.capability().model_id == _MODEL


def test_openai_adapter_still_defaults_to_openai_provider() -> None:
    # The generic adapter keeps its OpenAI identity (it serves OpenAI/Azure/vLLM
    # too) — renaming it to "openrouter" would be inaccurate (ADR 0030).
    adapter = OpenAIAdapter(api_key="k", model_id="gpt-4o")
    assert adapter.capability().provider == "openai"


# ── startup model-config self-check (stability hardening) ─────────────────────


class _Settings:
    """Minimal stand-in for the model-config fields check_model_config reads."""

    def __init__(self, **kw: object) -> None:
        self.default_model_provider = kw.get("default_model_provider", "ollama")
        self.default_model_id = kw.get("default_model_id", "qwen3:8b")
        self.default_model_api_key_ref = kw.get("default_model_api_key_ref", "")
        self.grounding_judge_model_id = kw.get("grounding_judge_model_id", "")
        self.grounding_judge_model_provider = kw.get("grounding_judge_model_provider", "")
        self.grounding_judge_api_key_ref = kw.get("grounding_judge_api_key_ref", "")


class _Secrets:
    def __init__(self, present: set[str]) -> None:
        self._present = present

    async def get(self, key: str) -> str | None:
        return "secret" if key in self._present else None


@pytest.mark.asyncio
async def test_config_check_passes_for_local_ollama() -> None:
    from wolf_server.agent.model_resolver import check_model_config

    problems = await check_model_config(_Settings(), _Secrets(set()))  # type: ignore[arg-type]
    assert problems == []


@pytest.mark.asyncio
async def test_config_check_passes_for_openrouter_with_resolvable_key() -> None:
    from wolf_server.agent.model_resolver import check_model_config

    settings = _Settings(
        default_model_provider="openrouter",
        default_model_id="openrouter/owl-alpha",
        default_model_api_key_ref="model.openrouter.api_key",
    )
    problems = await check_model_config(
        settings,  # type: ignore[arg-type]
        _Secrets({"model.openrouter.api_key"}),
    )
    assert problems == []


@pytest.mark.asyncio
async def test_config_check_flags_inline_comment_corruption() -> None:
    # The exact bug class: a stray inline '#' comment leaks into the value, so
    # the provider name is unknown AND the key ref doesn't resolve.
    from wolf_server.agent.model_resolver import check_model_config

    settings = _Settings(
        default_model_provider="openrouter  # OPENROUTER TEST",
        default_model_api_key_ref="model.openrouter.api_key  # revert: empty",
    )
    problems = await check_model_config(settings, _Secrets(set()))  # type: ignore[arg-type]
    assert len(problems) == 1
    assert "unknown provider" in problems[0]


@pytest.mark.asyncio
async def test_config_check_flags_unresolvable_key() -> None:
    from wolf_server.agent.model_resolver import check_model_config

    settings = _Settings(
        default_model_provider="openrouter",
        default_model_api_key_ref="model.openrouter.api_key",
    )
    problems = await check_model_config(settings, _Secrets(set()))  # type: ignore[arg-type]
    assert len(problems) == 1
    assert "did not resolve" in problems[0]


@pytest.mark.asyncio
async def test_config_check_validates_judge_independently() -> None:
    from wolf_server.agent.model_resolver import check_model_config

    settings = _Settings(
        default_model_provider="ollama",
        default_model_id="qwen3:8b",
        grounding_judge_model_id="openrouter/owl-alpha",
        grounding_judge_model_provider="openrouter",
        grounding_judge_api_key_ref="missing.key",
    )
    problems = await check_model_config(settings, _Secrets(set()))  # type: ignore[arg-type]
    assert len(problems) == 1
    assert problems[0].startswith("grounding judge")
