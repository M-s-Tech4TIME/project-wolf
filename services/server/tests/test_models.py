"""Tests for model adapters, tool registry, and structured-output fallback."""

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from wolf_schema import (
    ChatRequest,
    ChatResponse,
    ToolCall,
    ToolSchema,
)
from wolf_schema.capability import (
    AgentStrategy,
    CapabilityDescriptor,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)
from wolf_schema.chat import Message, MessageRole
from wolf_schema.tools import ToolTier

# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_response(body: dict[str, Any], status: int = 200) -> httpx.Response:
    # httpx.Response.raise_for_status() requires a request object to be set.
    request = httpx.Request("POST", "http://test.local")
    return httpx.Response(status, json=body, request=request)


def _simple_request(content: str = "Hello") -> ChatRequest:
    return ChatRequest(messages=[Message(role=MessageRole.user, content=content)])


def _simple_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        model_id="test-model",
        provider="test",
        context_window=8192,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.mid,
        structured_output=StructuredOutput.prompt_coaxed,
        max_safe_autonomous_steps=8,
        recommended_strategy=AgentStrategy.guided,
    )


# ── CapabilityDescriptor ──────────────────────────────────────────────────────


def test_capability_descriptor_fields() -> None:
    desc = _simple_descriptor()
    assert desc.model_id == "test-model"
    assert desc.reasoning_tier == ReasoningTier.mid
    assert desc.recommended_strategy == AgentStrategy.guided


def test_known_models_include_ollama() -> None:
    from wolf_server.models.interface import KNOWN_MODELS

    assert "llama3.2" in KNOWN_MODELS
    assert KNOWN_MODELS["llama3.2"].provider == "ollama"


def test_known_models_include_frontier() -> None:
    from wolf_server.models.interface import KNOWN_MODELS

    for model_id in ("claude-sonnet-4-6", "gpt-4o"):
        assert model_id in KNOWN_MODELS
        desc = KNOWN_MODELS[model_id]
        assert desc.reasoning_tier == ReasoningTier.frontier
        assert desc.recommended_strategy == AgentStrategy.frontier


def test_default_descriptor_unknown_model() -> None:
    from wolf_server.models.interface import default_descriptor_for

    desc = default_descriptor_for("some-unknown-model-7b", "ollama")
    assert desc.native_tool_calling == NativeToolCalling.none
    assert desc.reasoning_tier == ReasoningTier.basic
    assert desc.recommended_strategy == AgentStrategy.pipeline


# ── Structured-output fallback ────────────────────────────────────────────────


def test_parse_fallback_answer() -> None:
    from wolf_server.models.fallback import parse_fallback_response

    answer, call = parse_fallback_response('{"answer": "The answer is 42."}')
    assert answer == "The answer is 42."
    assert call is None


def test_parse_fallback_tool_call() -> None:
    from wolf_server.models.fallback import parse_fallback_response

    raw = json.dumps({"tool": "ping", "arguments": {}})
    answer, call = parse_fallback_response(raw)
    assert answer is None
    assert call is not None
    assert call.name == "ping"
    assert call.arguments == {}


def test_parse_fallback_strips_fences() -> None:
    from wolf_server.models.fallback import parse_fallback_response

    raw = '```json\n{"answer": "hi"}\n```'
    answer, call = parse_fallback_response(raw)
    assert answer == "hi"
    assert call is None


def test_parse_fallback_bad_json_raises() -> None:
    from wolf_server.models.fallback import parse_fallback_response

    with pytest.raises(ValueError, match="Not valid JSON"):
        parse_fallback_response("not json at all")


def test_parse_fallback_missing_key_raises() -> None:
    from wolf_server.models.fallback import parse_fallback_response

    with pytest.raises(ValueError, match="answer.*tool"):
        parse_fallback_response('{"something": "else"}')


@pytest.mark.asyncio
async def test_fallback_loop_retries_on_bad_json() -> None:
    from wolf_server.models.fallback import chat_with_fallback

    call_count = 0

    async def raw_chat(request: ChatRequest) -> ChatResponse:
        nonlocal call_count
        call_count += 1
        content = "not json" if call_count < 2 else '{"answer": "recovered"}'
        return ChatResponse(
            content=content,
            tool_calls=[],
            input_tokens=10,
            output_tokens=5,
            stop_reason="stop",
            model_id="test",
        )

    req = _simple_request("test")
    result = await chat_with_fallback(raw_chat, req)
    assert result.content == "recovered"
    assert call_count == 2


@pytest.mark.asyncio
async def test_fallback_loop_exhaustion_raises() -> None:
    from wolf_common.errors import WolfError
    from wolf_server.models.fallback import chat_with_fallback

    async def raw_chat(request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content="always bad",
            tool_calls=[],
            input_tokens=10,
            output_tokens=5,
            stop_reason="stop",
            model_id="test",
        )

    with pytest.raises(WolfError, match="failed structured-output fallback"):
        await chat_with_fallback(raw_chat, _simple_request())


# ── Tool registry ─────────────────────────────────────────────────────────────


def _make_tool(name: str, tier: ToolTier = ToolTier.read) -> ToolSchema:
    return ToolSchema(
        name=name,
        description=f"Tool {name}",
        tier=tier,
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object"},
    )


def test_registry_register_and_lookup() -> None:
    from wolf_server.models.registry import ToolRegistry

    reg = ToolRegistry()
    tool = _make_tool("search_alerts")
    reg.register(tool)
    found = reg.lookup("search_alerts")
    assert found.name == "search_alerts"


def test_registry_duplicate_raises() -> None:
    from wolf_server.models.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(_make_tool("ping"))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_make_tool("ping"))


def test_registry_lookup_unknown_raises() -> None:
    from wolf_common.errors import ToolNotFoundError
    from wolf_server.models.registry import ToolRegistry

    reg = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        reg.lookup("does_not_exist")


def test_model_tools_excludes_execute() -> None:
    from wolf_server.models.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(_make_tool("search_alerts", ToolTier.read))
    reg.register(_make_tool("propose_action", ToolTier.propose))
    reg.register(_make_tool("execute_active_response", ToolTier.execute))

    visible = reg.model_tools()
    names = {t.name for t in visible}
    assert "search_alerts" in names
    assert "propose_action" in names
    assert "execute_active_response" not in names


def test_validate_model_call_rejects_execute() -> None:
    from wolf_common.errors import ToolCapabilityError
    from wolf_server.models.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(_make_tool("execute_active_response", ToolTier.execute))
    with pytest.raises(ToolCapabilityError, match="execute-tier"):
        reg.validate_model_call("execute_active_response")


# ── Anthropic adapter ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_anthropic_adapter_chat() -> None:
    from wolf_server.models.anthropic import AnthropicAdapter

    body = {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello from Claude"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))

    adapter = AnthropicAdapter(api_key="test-key", model_id="claude-sonnet-4-6", client=mock_client)
    response = await adapter.chat(_simple_request())

    assert response.content == "Hello from Claude"
    assert response.input_tokens == 10
    assert response.output_tokens == 5
    assert response.model_id == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_anthropic_adapter_tool_call() -> None:
    from wolf_server.models.anthropic import AnthropicAdapter

    tool_id = str(uuid.uuid4())
    body = {
        "id": "msg_456",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": tool_id,
                "name": "search_alerts",
                "input": {"query": "ssh failure"},
            }
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 20, "output_tokens": 15},
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))

    adapter = AnthropicAdapter(api_key="test-key", model_id="claude-sonnet-4-6", client=mock_client)
    ping = _make_tool("search_alerts")
    request = ChatRequest(
        messages=[Message(role=MessageRole.user, content="Find SSH failures")],
        tools=[ping],
    )
    response = await adapter.chat(request)

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "search_alerts"
    assert response.tool_calls[0].arguments == {"query": "ssh failure"}


# ── OpenAI adapter ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openai_adapter_chat() -> None:
    from wolf_server.models.openai import OpenAIAdapter

    body = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "Hello from GPT"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))

    adapter = OpenAIAdapter(api_key="test-key", model_id="gpt-4o", client=mock_client)
    response = await adapter.chat(_simple_request())

    assert response.content == "Hello from GPT"
    assert response.input_tokens == 10
    assert response.output_tokens == 5


@pytest.mark.asyncio
async def test_openai_adapter_tool_call() -> None:
    from wolf_server.models.openai import OpenAIAdapter

    tool_id = str(uuid.uuid4())
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_id,
                            "type": "function",
                            "function": {
                                "name": "list_agents",
                                "arguments": '{"status": "active"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 15, "completion_tokens": 10},
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))

    adapter = OpenAIAdapter(api_key="test-key", model_id="gpt-4o", client=mock_client)
    response = await adapter.chat(_simple_request("List active agents"))

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "list_agents"
    assert response.tool_calls[0].arguments == {"status": "active"}


# ── Ollama adapter ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ollama_adapter_chat() -> None:
    from wolf_server.models.ollama import OllamaAdapter

    body = {
        "message": {"role": "assistant", "content": "Hello from Ollama"},
        "done": True,
        "prompt_eval_count": 8,
        "eval_count": 4,
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))

    adapter = OllamaAdapter(model_id="llama3.2", client=mock_client)
    response = await adapter.chat(_simple_request())

    assert response.content == "Hello from Ollama"
    assert response.input_tokens == 8
    assert response.output_tokens == 4


@pytest.mark.asyncio
async def test_ollama_adapter_tool_call() -> None:
    from wolf_server.models.ollama import OllamaAdapter

    body = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "ping",
                        "arguments": {},
                    }
                }
            ],
        },
        "done": True,
        "prompt_eval_count": 12,
        "eval_count": 6,
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))

    adapter = OllamaAdapter(model_id="llama3.2", client=mock_client)
    response = await adapter.chat(_simple_request("Call ping"))

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "ping"


@pytest.mark.asyncio
async def test_ollama_adapter_uses_fallback_for_no_tool_calling() -> None:
    """Ollama adapter on a 'none' tool-calling model must route through fallback."""
    from wolf_server.models.ollama import OllamaAdapter

    # llama3.2:1b has native_tool_calling=none in KNOWN_MODELS
    body = {
        "message": {
            "role": "assistant",
            "content": '{"answer": "I cannot call tools natively"}',
        },
        "done": True,
        "prompt_eval_count": 5,
        "eval_count": 10,
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))

    adapter = OllamaAdapter(model_id="llama3.2:1b", client=mock_client)
    request = ChatRequest(
        messages=[Message(role=MessageRole.user, content="Answer this")],
        tools=[_make_tool("some_tool")],
    )
    response = await adapter.chat(request)
    # The fallback should parse the answer key
    assert "cannot call tools" in response.content


@pytest.mark.asyncio
async def test_ollama_adapter_multi_turn_with_tools() -> None:
    """Covers Ollama message building for tool results and assistant+tool_calls history."""
    from wolf_schema import ToolResult
    from wolf_server.models.ollama import OllamaAdapter

    body = {
        "message": {"role": "assistant", "content": "All done."},
        "done": True,
        "prompt_eval_count": 20,
        "eval_count": 8,
    }
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))
    adapter = OllamaAdapter(model_id="llama3.2", client=mock_client)

    request = ChatRequest(
        messages=[
            Message(role=MessageRole.user, content="Search for alerts."),
            Message(
                role=MessageRole.assistant,
                content="Calling search.",
                tool_calls=[ToolCall(id="tc-oll", name="search_alerts", arguments={"q": "ssh"})],
            ),
            Message(
                role=MessageRole.tool,
                tool_results=[
                    ToolResult(
                        tool_call_id="tc-oll",
                        name="search_alerts",
                        content={"count": 5},  # non-string content → str() conversion
                    )
                ],
            ),
        ],
        tools=[_make_tool("search_alerts")],  # covers _canonical_to_ollama_tool
    )
    response = await adapter.chat(request)
    assert response.content == "All done."

    # Verify tools were included in the payload
    payload = mock_client.post.call_args.kwargs["json"]
    assert "tools" in payload


@pytest.mark.asyncio
async def test_ollama_adapter_non_dict_arguments_fallback() -> None:
    """Covers the defensive branch: non-dict tool arguments become empty dict."""
    from wolf_server.models.ollama import OllamaAdapter

    body = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "ping", "arguments": "not-a-dict"}}],
        },
        "done": True,
        "prompt_eval_count": 5,
        "eval_count": 3,
    }
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))
    adapter = OllamaAdapter(model_id="llama3.2", client=mock_client)
    response = await adapter.chat(_simple_request())
    assert response.tool_calls[0].name == "ping"
    assert response.tool_calls[0].arguments == {}


# ── Adapter capability() and stream() ────────────────────────────────────────


def test_adapter_capability_methods() -> None:
    from unittest.mock import MagicMock

    from wolf_schema.capability import ReasoningTier
    from wolf_server.models.anthropic import AnthropicAdapter
    from wolf_server.models.ollama import OllamaAdapter
    from wolf_server.models.openai import OpenAIAdapter

    mock_client = MagicMock()
    ant = AnthropicAdapter(api_key="k", model_id="claude-sonnet-4-6", client=mock_client)
    assert ant.capability().provider == "anthropic"
    assert ant.capability().reasoning_tier == ReasoningTier.frontier

    oai = OpenAIAdapter(api_key="k", model_id="gpt-4o", client=mock_client)
    assert oai.capability().provider == "openai"

    oll = OllamaAdapter(model_id="llama3.2", client=mock_client)
    assert oll.capability().provider == "ollama"


@pytest.mark.asyncio
async def test_anthropic_adapter_stream() -> None:
    from wolf_server.models.anthropic import AnthropicAdapter

    body = {
        "id": "msg_s1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Streamed chunk"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))
    adapter = AnthropicAdapter(api_key="k", model_id="claude-sonnet-4-6", client=mock_client)
    chunks = [c async for c in adapter.stream(_simple_request())]
    assert "Streamed chunk" in "".join(chunks)


@pytest.mark.asyncio
async def test_openai_adapter_stream() -> None:
    from wolf_server.models.openai import OpenAIAdapter

    body = {
        "choices": [
            {"message": {"role": "assistant", "content": "OAI stream"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))
    adapter = OpenAIAdapter(api_key="k", model_id="gpt-4o", client=mock_client)
    chunks = [c async for c in adapter.stream(_simple_request())]
    assert "OAI stream" in "".join(chunks)


@pytest.mark.asyncio
async def test_ollama_adapter_stream() -> None:
    from wolf_server.models.ollama import OllamaAdapter

    body = {
        "message": {"role": "assistant", "content": "Ollama stream"},
        "done": True,
        "prompt_eval_count": 5,
        "eval_count": 3,
    }
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))
    adapter = OllamaAdapter(model_id="llama3.2", client=mock_client)
    chunks = [c async for c in adapter.stream(_simple_request())]
    assert "Ollama stream" in "".join(chunks)


# ── Adapter multi-turn conversation (covers message-building edge cases) ───────


@pytest.mark.asyncio
async def test_anthropic_adapter_multi_turn_conversation() -> None:
    """Covers system messages, assistant+tool_calls history, tool results."""
    from wolf_schema import ToolResult
    from wolf_server.models.anthropic import AnthropicAdapter

    body = {
        "id": "msg_mt",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Investigation complete."}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 50, "output_tokens": 10},
    }
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))
    adapter = AnthropicAdapter(api_key="k", model_id="claude-sonnet-4-6", client=mock_client)

    request = ChatRequest(
        messages=[
            Message(role=MessageRole.system, content="You are a SOC analyst assistant."),
            Message(role=MessageRole.user, content="Investigate the alert."),
            Message(
                role=MessageRole.assistant,
                content="Searching alerts.",
                tool_calls=[ToolCall(id="tc-mt", name="search_alerts", arguments={"q": "test"})],
            ),
            Message(
                role=MessageRole.tool,
                tool_results=[
                    ToolResult(
                        tool_call_id="tc-mt",
                        name="search_alerts",
                        content="2 alerts found",
                    )
                ],
            ),
        ],
    )
    response = await adapter.chat(request)
    assert response.content == "Investigation complete."

    # Verify system prompt was extracted from messages and sent separately
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["system"] == "You are a SOC analyst assistant."


@pytest.mark.asyncio
async def test_openai_adapter_multi_turn_conversation() -> None:
    """Covers assistant+tool_calls and tool results in message history."""
    from wolf_schema import ToolResult
    from wolf_server.models.openai import OpenAIAdapter

    body = {
        "choices": [
            {"message": {"role": "assistant", "content": "Done."}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 30, "completion_tokens": 5},
    }
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))
    adapter = OpenAIAdapter(api_key="k", model_id="gpt-4o", client=mock_client)

    request = ChatRequest(
        messages=[
            Message(role=MessageRole.user, content="Check agents."),
            Message(
                role=MessageRole.assistant,
                content="",
                tool_calls=[ToolCall(id="tc-oai", name="list_agents", arguments={})],
            ),
            Message(
                role=MessageRole.tool,
                tool_results=[
                    ToolResult(
                        tool_call_id="tc-oai",
                        name="list_agents",
                        content={"agents": ["agent-001"]},
                    )
                ],
            ),
        ],
    )
    response = await adapter.chat(request)
    assert response.content == "Done."


@pytest.mark.asyncio
async def test_anthropic_adapter_uses_fallback_for_no_tool_calling() -> None:
    """Anthropic adapter on a model with NativeToolCalling.none routes through fallback."""
    from wolf_server.models.anthropic import AnthropicAdapter

    body = {
        "id": "msg_fb",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": '{"answer": "fallback answer"}'}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(body))

    # Use an unknown model that gets the conservative descriptor (native_tool_calling=none)
    adapter = AnthropicAdapter(api_key="k", model_id="unknown-tiny-model", client=mock_client)
    request = ChatRequest(
        messages=[Message(role=MessageRole.user, content="Answer this")],
        tools=[_make_tool("some_tool")],
    )
    response = await adapter.chat(request)
    assert "fallback answer" in response.content


# ── ModelProvider Protocol compliance ─────────────────────────────────────────


def test_adapters_satisfy_protocol() -> None:

    from wolf_server.models.anthropic import AnthropicAdapter
    from wolf_server.models.interface import ModelProvider
    from wolf_server.models.ollama import OllamaAdapter
    from wolf_server.models.openai import OpenAIAdapter

    mock_client = MagicMock()
    cases = [
        (
            AnthropicAdapter,
            {"api_key": "k", "model_id": "claude-sonnet-4-6", "client": mock_client},
        ),
        (OpenAIAdapter, {"api_key": "k", "model_id": "gpt-4o", "client": mock_client}),
        (OllamaAdapter, {"model_id": "llama3.2", "client": mock_client}),
    ]
    for adapter_class, kwargs in cases:
        adapter = adapter_class(**kwargs)
        assert isinstance(adapter, ModelProvider), (
            f"{adapter_class.__name__} does not satisfy ModelProvider"
        )
