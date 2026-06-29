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


async def get_model_for_organization(
    _ctx: OrganizationContext,
    settings: Settings,
    secrets: SecretsBackend,
) -> ModelProvider:
    """Return the configured chat ModelProvider for a request.

    The organization context is accepted (and reserved) so per-organization model
    selection can be added without changing the call site.
    """
    return await _build_provider(
        provider_name=settings.default_model_provider,
        model_id=settings.default_model_id,
        api_key_ref=settings.default_model_api_key_ref,
        settings=settings,
        secrets=secrets,
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
    return await _build_provider(
        provider_name=judge_provider_name,
        model_id=settings.grounding_judge_model_id,
        api_key_ref=judge_api_key_ref,
        settings=settings,
        secrets=secrets,
        # The judge's prompt + 5 KB evidence + claims can exceed qwen3:8b's
        # default 4 K context on Ollama, which manifests as empty model
        # output and a "JSONDecodeError on empty string" downstream. 8 K
        # comfortably fits typical grounding inputs without exceeding
        # qwen3:8b's actual 32 K capability. Ignored for non-Ollama
        # providers. Slice 5.0b.4.
        ollama_num_ctx=8192,
    )
