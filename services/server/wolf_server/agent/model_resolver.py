"""Resolve a `ModelProvider` for a organization.

Phase 2B: returns the process-default model configured via settings.
Per-organization model configuration (a `OrganizationModelConfig` table mirroring
`OrganizationWazuhConfig`) is a later-phase enhancement; the function signature
already accepts a OrganizationContext so call sites do not need to change.

Doc 02 §Per-organization model choice — each organization can run a different model;
that capability is not exercised here yet, but the seam is.
"""

import structlog
from wolf_common.errors import WolfError
from wolf_secrets.interface import SecretsBackend

from wolf_server.config import Settings
from wolf_server.models.anthropic import AnthropicAdapter
from wolf_server.models.failover import FailoverProvider
from wolf_server.models.interface import ModelProvider
from wolf_server.models.ollama import OllamaAdapter
from wolf_server.models.openai import OpenAIAdapter
from wolf_server.models.openrouter import OpenRouterAdapter
from wolf_server.organization.context import OrganizationContext

logger = structlog.get_logger(__name__)


class ModelProviderUnconfiguredError(WolfError):
    """The settings or secrets do not contain a usable model configuration."""

    http_status = 500
    error_code = "model_provider_unconfigured"


async def _build_provider(
    *,
    provider_name: str,
    model_id: str,
    api_key_ref: str,
    settings: Settings,
    secrets: SecretsBackend,
    ollama_num_ctx: int | None = None,
) -> ModelProvider:
    """Construct a ModelProvider from name + id + (optional) secret ref."""
    api_key = ""
    if api_key_ref:
        secret = await secrets.get(api_key_ref)
        if secret is None:
            raise ModelProviderUnconfiguredError(f"Secret {api_key_ref!r} not found")
        api_key = secret

    match provider_name.lower():
        case "anthropic":
            if not api_key:
                raise ModelProviderUnconfiguredError(
                    "Anthropic provider requires an API key reference"
                )
            return AnthropicAdapter(api_key=api_key, model_id=model_id)
        case "openai":
            if not api_key:
                raise ModelProviderUnconfiguredError(
                    "OpenAI provider requires an API key reference"
                )
            return OpenAIAdapter(
                api_key=api_key,
                model_id=model_id,
                base_url=settings.openai_base_url,
            )
        case "openrouter":
            # OpenRouter = OpenAI-compatible hosted models. The OpenAIAdapter
            # carries the chat + real SSE streaming + 429 handling; here we just
            # point it at OpenRouter's base + add attribution headers. Selectable
            # option (ADR 0030); local Ollama stays the default. Free models are
            # $0 but daily-capped (a 429 surfaces ModelProviderRateLimitError).
            if not api_key:
                raise ModelProviderUnconfiguredError(
                    "OpenRouter provider requires an API key reference (store it via "
                    "`python -m wolf_server.management.set_secret` and set the *_API_KEY_REF)"
                )
            return OpenRouterAdapter(
                api_key=api_key,
                model_id=model_id,
                base_url=settings.openrouter_base_url,
                referer=settings.openrouter_referer,
                title=settings.openrouter_title,
            )
        case "ollama":
            return OllamaAdapter(
                model_id=model_id,
                base_url=settings.ollama_base_url,
                num_ctx=ollama_num_ctx,
            )
        case _:
            raise ModelProviderUnconfiguredError(f"Unknown model provider: {provider_name!r}")


async def _build_with_optional_fallback(
    *,
    provider_name: str,
    model_id: str,
    api_key_ref: str,
    settings: Settings,
    secrets: SecretsBackend,
    ollama_num_ctx: int | None = None,
) -> ModelProvider:
    """Build the primary provider, wrapping it in a :class:`FailoverProvider`
    with the configured fallback when ``FALLBACK_MODEL_ID`` is set.

    The chain is skipped (primary returned bare) when no fallback is configured,
    or when the fallback resolves to the very same provider+model as the primary
    (a pointless self-chain). The fallback provider defaults to ``ollama`` — the
    intended local safety net — when a fallback id is set without a provider.
    """
    primary = await _build_provider(
        provider_name=provider_name,
        model_id=model_id,
        api_key_ref=api_key_ref,
        settings=settings,
        secrets=secrets,
        ollama_num_ctx=ollama_num_ctx,
    )
    if not settings.fallback_model_id:
        return primary
    fallback_provider = settings.fallback_model_provider or "ollama"
    same_provider = fallback_provider.lower() == provider_name.lower()
    if same_provider and settings.fallback_model_id == model_id:
        return primary
    fallback = await _build_provider(
        provider_name=fallback_provider,
        model_id=settings.fallback_model_id,
        api_key_ref=settings.fallback_model_api_key_ref,
        settings=settings,
        secrets=secrets,
        ollama_num_ctx=ollama_num_ctx,
    )
    return FailoverProvider(providers=[primary, fallback])


# Providers Wolf knows how to build (mirrors the match in _build_provider) and
# the subset that REQUIRE an API-key reference (ollama is keyless/local).
_KNOWN_PROVIDERS = frozenset({"anthropic", "openai", "openrouter", "ollama"})
_KEYED_PROVIDERS = frozenset({"anthropic", "openai", "openrouter"})


async def check_model_config(settings: Settings, secrets: SecretsBackend) -> list[str]:
    """Validate the configured chat + grounding-judge model providers at startup.

    Returns a list of human-readable problems (empty = healthy). Catches the
    failure modes that otherwise surface only as a per-request HTTP 500 in the
    chat path — an unknown provider name or an API-key ref that resolves to no
    secret. The classic cause is a stray inline ``#`` comment on an env value
    line (systemd ``EnvironmentFile`` keeps it as part of the value), so the
    hint calls that out. Used by the startup self-check so a broken model config
    fails LOUDLY at boot instead of silently 500-ing every chat.
    """

    async def _check(label: str, provider: str, model_id: str, key_ref: str) -> None:
        name = (provider or "").strip().lower()
        if name not in _KNOWN_PROVIDERS:
            problems.append(
                f"{label}: unknown provider {provider!r} (known: "
                f"{', '.join(sorted(_KNOWN_PROVIDERS))}). A stray inline '#' comment on the "
                "env value line is the usual cause (systemd keeps it in the value)."
            )
            return
        if name in _KEYED_PROVIDERS:
            if not key_ref:
                problems.append(
                    f"{label}: provider {name!r} (model {model_id!r}) needs an API-key ref "
                    "but none is configured."
                )
                return
            try:
                secret = await secrets.get(key_ref)
            except Exception as exc:  # noqa: BLE001 — report, never crash the check
                problems.append(f"{label}: error resolving API-key ref {key_ref!r}: {exc}")
                return
            if secret is None:
                problems.append(
                    f"{label}: API-key ref {key_ref!r} did not resolve to a secret — store it "
                    "via `python -m wolf_server.management.set_secret`, and check the env value "
                    "has no inline '#' comment."
                )

    problems: list[str] = []
    await _check(
        "chat model",
        settings.default_model_provider,
        settings.default_model_id,
        settings.default_model_api_key_ref,
    )
    if settings.grounding_judge_model_id:
        await _check(
            "grounding judge",
            settings.grounding_judge_model_provider or settings.default_model_provider,
            settings.grounding_judge_model_id,
            settings.grounding_judge_api_key_ref or settings.default_model_api_key_ref,
        )
    if settings.fallback_model_id:
        await _check(
            "fallback model",
            settings.fallback_model_provider or "ollama",
            settings.fallback_model_id,
            settings.fallback_model_api_key_ref,
        )
    return problems


async def get_model_for_organization(
    _ctx: OrganizationContext,
    settings: Settings,
    secrets: SecretsBackend,
) -> ModelProvider:
    """Return the configured chat ModelProvider for a request.

    The organization context is accepted (and reserved) so per-organization model
    selection can be added without changing the call site.

    When a fallback model is configured (FALLBACK_MODEL_ID), the returned
    provider is a FailoverProvider — the primary with an automatic safety net.
    """
    return await _build_with_optional_fallback(
        provider_name=settings.default_model_provider,
        model_id=settings.default_model_id,
        api_key_ref=settings.default_model_api_key_ref,
        settings=settings,
        secrets=secrets,
        # Size the Ollama context to hold Wolf's system prompt + full tool
        # catalog (~7.2K tokens) plus multi-step headroom. Without this the
        # adapter falls back to Ollama's 4096 default, which TRUNCATES the tool
        # definitions off the head of the prompt — the model then can't see
        # tools like list_agents and answers "no such tool" (2026-07-01
        # regression). Ignored by non-Ollama providers.
        ollama_num_ctx=settings.ollama_num_ctx,
    )


async def get_grounding_judge_model(
    _ctx: OrganizationContext,
    settings: Settings,
    secrets: SecretsBackend,
    *,
    fallback_chat_provider: ModelProvider,
) -> ModelProvider:
    """Return a ModelProvider for the grounding validator.

    Defaults to the same provider as the chat model — single-model
    deployments stay simple. When `GROUNDING_JUDGE_MODEL_ID` is set, builds
    a separate provider so chat and judge can be different models (typical
    production posture: small fast chat model + larger judge).
    """
    if not settings.grounding_judge_model_id:
        return fallback_chat_provider
    judge_provider_name = settings.grounding_judge_model_provider or settings.default_model_provider
    judge_api_key_ref = settings.grounding_judge_api_key_ref or settings.default_model_api_key_ref
    # The judge gets the same failover safety net as chat (FALLBACK_MODEL_ID),
    # so grounding survives a capped/erroring hosted judge instead of silently
    # degrading to no verdicts. ollama_num_ctx applies to any Ollama link.
    return await _build_with_optional_fallback(
        provider_name=judge_provider_name,
        model_id=settings.grounding_judge_model_id,
        api_key_ref=judge_api_key_ref,
        settings=settings,
        secrets=secrets,
        # The judge's prompt + 5 KB evidence + claims can exceed Ollama's 4 K
        # default context, which manifests as empty model output and a
        # "JSONDecodeError on empty string" downstream (Slice 5.0b.4). Share the
        # single ollama_num_ctx knob with the chat model: when chat and judge
        # are the SAME Ollama tag (the default unified qwen3:8b) one loaded
        # context serves both, so Ollama never reloads the model between a chat
        # call and its grounding pass. Ignored for non-Ollama providers.
        ollama_num_ctx=settings.ollama_num_ctx,
    )
