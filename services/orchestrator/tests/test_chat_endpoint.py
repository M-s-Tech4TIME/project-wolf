"""End-to-end test for POST /api/v1/chat.

Exercises the full request path:
  cookie auth → tenant context → secrets/wazuh/model resolution → agent loop
  → response payload with citations.

The Wazuh + model resolution paths are monkey-patched so the test can run
hermetically without a real Wazuh deployment or a model API key.
"""

import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from wolf_schema import CapabilityDescriptor, ChatRequest, ChatResponse, ToolCall
from wolf_schema.capability import (
    AgentStrategy,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    """Tools registered at app startup leak across tests — reset here."""
    from app.models.registry import registry as schema_registry
    from app.tools.registry import runtime_registry

    schema_registry.clear()
    runtime_registry.clear()
    # Re-register the canonical read tools so the chat path has a tool catalog.
    from app.tools.registration import register_all_read_tools

    register_all_read_tools()
    yield
    schema_registry.clear()
    runtime_registry.clear()


def _descriptor(strategy: AgentStrategy = AgentStrategy.frontier) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        model_id="mock-claude",
        provider="mock",
        context_window=8192,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.frontier,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=5,
        recommended_strategy=strategy,
    )


class _MockProvider:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self._cap = _descriptor()
        self.call_count = 0

    def capability(self) -> CapabilityDescriptor:
        return self._cap

    async def chat(self, _request: ChatRequest) -> ChatResponse:
        response = self._responses[self.call_count]
        self.call_count += 1
        return response

    def stream(self, _request: ChatRequest) -> Any:
        raise NotImplementedError


def _fake_wazuh_connection(tenant_id: uuid.UUID) -> Any:
    from app.wazuh.config import WazuhConnection

    return WazuhConnection(
        tenant_id=tenant_id,
        opensearch_url="https://os.example.test:9200",
        opensearch_index_pattern="wazuh-alerts-*",
        opensearch_username="ro",
        opensearch_password="x",  # noqa: S106 — test fixture
        server_api_url="https://api.example.test:55000",
        server_api_username="api",
        server_api_password="x",  # noqa: S106 — test fixture
        verify_tls=True,
    )


def _patch_chat_module(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: _MockProvider,
    tenant_id: uuid.UUID,
) -> None:
    """Swap out the chat endpoint's external resolvers with hermetic fakes."""

    async def _resolver(*_args: Any, **_kwargs: Any) -> _MockProvider:
        return provider

    async def _wazuh(*_args: Any, **_kwargs: Any) -> Any:
        return _fake_wazuh_connection(tenant_id)

    def _os_client(*_args: Any, **_kwargs: Any) -> MagicMock:
        client = MagicMock()
        client.query_builder.search_alerts.return_value = {
            "query": {"bool": {"filter": []}}
        }
        client.execute = AsyncMock(
            return_value={"hits": {"total": {"value": 0}, "hits": []}}
        )
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        return client

    def _api_client(*_args: Any, **_kwargs: Any) -> MagicMock:
        client = MagicMock()
        client.get = AsyncMock(return_value={"data": {"affected_items": []}})
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        return client

    monkeypatch.setattr("app.api.chat.get_model_for_tenant", _resolver)
    monkeypatch.setattr("app.api.chat.get_wazuh_connection", _wazuh)
    monkeypatch.setattr("app.api.chat.WazuhOpenSearchClient", _os_client)
    monkeypatch.setattr("app.api.chat.WazuhServerApiClient", _api_client)


class _StubSecrets:
    """An in-memory SecretsBackend stub — chat tests do not need real secrets."""

    async def get(self, _key: str) -> str | None:
        return None

    async def set(self, _key: str, _value: str) -> None:
        return None

    async def delete(self, _key: str) -> None:
        return None

    async def exists(self, _key: str) -> bool:
        return False


async def _login(client: AsyncClient, seed: dict[str, Any]) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "email": seed["user_email"],
            "password": "password123",
            "tenant_id": str(seed["tenant_id"]),
        },
    )
    assert resp.status_code == 200


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_unauthenticated_returns_401(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/chat", json={"question": "hi"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_returns_grounded_answer_with_citations(
    client: AsyncClient,
    seed_tenant_and_user: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = ToolCall(
        id="c-1",
        name="search_alerts",
        arguments={
            "time_from": "2026-05-21T00:00:00+00:00",
            "time_to": "2026-05-21T23:59:59+00:00",
        },
    )
    provider = _MockProvider(
        [
            ChatResponse(
                content="",
                tool_calls=[call],
                input_tokens=10,
                output_tokens=20,
                stop_reason="tool_use",
                model_id="mock-claude",
            ),
            ChatResponse(
                content="No alerts found in the window.",
                tool_calls=[],
                input_tokens=15,
                output_tokens=25,
                stop_reason="end_turn",
                model_id="mock-claude",
            ),
        ]
    )
    _patch_chat_module(
        monkeypatch, provider=provider, tenant_id=seed_tenant_and_user["tenant_id"]
    )
    monkeypatch.setattr("app.api.chat._secrets_dep", lambda: _StubSecrets())

    await _login(client, seed_tenant_and_user)

    resp = await client.post("/api/v1/chat", json={"question": "anything today?"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"] == "No alerts found in the window."
    assert body["stop_reason"] == "answer"
    assert body["step_count"] == 2
    assert body["tool_call_count"] == 1
    assert body["strategy"] == "frontier"
    assert body["model_id"] == "mock-claude"
    assert len(body["citations"]) == 1
    assert body["citations"][0]["tool"] == "search_alerts"
    assert body["input_tokens"] == 25  # 10 + 15
    assert body["output_tokens"] == 45  # 20 + 25


@pytest.mark.asyncio
async def test_chat_validates_request_body(
    client: AsyncClient,
    seed_tenant_and_user: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _MockProvider(
        [
            ChatResponse(
                content="ok",
                tool_calls=[],
                input_tokens=1,
                output_tokens=1,
                stop_reason="end_turn",
                model_id="mock-claude",
            )
        ]
    )
    _patch_chat_module(
        monkeypatch, provider=provider, tenant_id=seed_tenant_and_user["tenant_id"]
    )
    monkeypatch.setattr("app.api.chat._secrets_dep", lambda: _StubSecrets())

    await _login(client, seed_tenant_and_user)

    # Empty question rejected by Pydantic min_length=1.
    resp = await client.post("/api/v1/chat", json={"question": ""})
    assert resp.status_code == 422
