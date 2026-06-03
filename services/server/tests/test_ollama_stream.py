"""Unit tests for OllamaAdapter.chat_stream (Slice 5.0c-d).

Uses httpx.MockTransport to intercept the Ollama HTTP call and feed back
a hand-crafted newline-delimited JSON stream. Asserts the adapter:

  - yields one ChatStreamDelta per non-empty content chunk;
  - aggregates the deltas into the final ChatResponse.content;
  - carries tool_calls + token counts through from the final done chunk;
  - emits exactly one ChatStreamDone terminator.

Avoids `respx` (not in the dependency set) by using httpx's built-in
MockTransport — no new third-party dep needed.
"""

import json

import httpx
import pytest
from wolf_schema import ChatRequest
from wolf_schema.chat import Message, MessageRole
from wolf_server.models.interface import ChatStreamDelta, ChatStreamDone
from wolf_server.models.ollama import OllamaAdapter


def _ollama_chunks(chunks: list[dict[str, object]]) -> bytes:
    """Encode a sequence of Ollama stream chunks as newline-delimited JSON."""
    return b"".join(json.dumps(c).encode() + b"\n" for c in chunks)


def _make_adapter(handler: httpx.MockTransport) -> OllamaAdapter:
    return OllamaAdapter(
        model_id="qwen3:4b",
        client=httpx.AsyncClient(transport=handler, base_url="http://test.local"),
    )


@pytest.mark.asyncio
async def test_chat_stream_yields_one_delta_per_non_empty_chunk() -> None:
    chunks = [
        {"message": {"content": "Hi"}, "done": False},
        {"message": {"content": " there"}, "done": False},
        {
            "message": {"content": ""},
            "done": True,
            "prompt_eval_count": 10,
            "eval_count": 20,
        },
    ]
    body = _ollama_chunks(chunks)

    def handler(request: httpx.Request) -> httpx.Response:
        # Verify the adapter actually flips stream:true on the payload.
        payload = json.loads(request.content)
        assert payload["stream"] is True
        return httpx.Response(200, content=body)

    adapter = _make_adapter(httpx.MockTransport(handler))
    req = ChatRequest(messages=[Message(role=MessageRole.user, content="hi")])

    deltas: list[str] = []
    done = None
    async for event in adapter.chat_stream(req):
        if isinstance(event, ChatStreamDelta):
            deltas.append(event.content_delta)
        else:
            done = event
    await adapter._client.aclose()

    assert deltas == ["Hi", " there"]
    assert isinstance(done, ChatStreamDone)
    assert done.response.content == "Hi there"
    assert done.response.input_tokens == 10
    assert done.response.output_tokens == 20
    assert done.response.stop_reason == "stop"


@pytest.mark.asyncio
async def test_chat_stream_carries_tool_calls_through_to_done() -> None:
    """A stream where the model emits a tool call instead of prose."""
    chunks = [
        # First chunk: just a tool call, no content. Adapter must NOT
        # yield a delta for empty content but MUST capture the tool call.
        {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "search_alerts", "arguments": {"size": 5}}}
                ],
            },
            "done": False,
        },
        {
            "message": {"content": ""},
            "done": True,
            "prompt_eval_count": 7,
            "eval_count": 3,
        },
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_ollama_chunks(chunks))

    adapter = _make_adapter(httpx.MockTransport(handler))
    req = ChatRequest(messages=[Message(role=MessageRole.user, content="alerts?")])

    deltas: list[str] = []
    done = None
    async for event in adapter.chat_stream(req):
        if isinstance(event, ChatStreamDelta):
            deltas.append(event.content_delta)
        else:
            done = event
    await adapter._client.aclose()

    assert deltas == []  # no prose -> no delta
    assert isinstance(done, ChatStreamDone)
    assert len(done.response.tool_calls) == 1
    assert done.response.tool_calls[0].name == "search_alerts"
    assert done.response.tool_calls[0].arguments == {"size": 5}


@pytest.mark.asyncio
async def test_chat_stream_skips_malformed_lines() -> None:
    """A misbehaving proxy that chunks weirdly mustn't kill the stream."""
    body = (
        b'{"message": {"content": "ok"}, "done": false}\n'
        b"this is not json at all\n"
        b'\n'  # blank line
        b'{"message": {"content": "!"}, "done": true, '
        b'"prompt_eval_count": 1, "eval_count": 2}\n'
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    adapter = _make_adapter(httpx.MockTransport(handler))
    req = ChatRequest(messages=[Message(role=MessageRole.user, content="hi")])

    deltas: list[str] = []
    done = None
    async for event in adapter.chat_stream(req):
        if isinstance(event, ChatStreamDelta):
            deltas.append(event.content_delta)
        else:
            done = event
    await adapter._client.aclose()

    assert deltas == ["ok", "!"]
    assert isinstance(done, ChatStreamDone)
    assert done.response.content == "ok!"
