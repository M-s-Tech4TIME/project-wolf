"""Resolve a `ModelProvider` for a tenant.

Phase 2B: returns the process-default model configured via settings.
Per-tenant model configuration (a `TenantModelConfig` table mirroring
`TenantWazuhConfig`) is a later-phase enhancement; the function signature
already accepts a TenantContext so call sites do not need to change.

Doc 02 §Per-tenant model choice — each tenant can run a different model;
that capability is not exercised here yet, but the seam is.
"""

import structlog
from wolf_common.errors import WolfError
from wolf_secrets.interface import SecretsBackend

from app.config import Settings
from app.models.anthropic import AnthropicAdapter
from app.models.interface import ModelProvider
from app.models.ollama import OllamaAdapter
from app.models.openai import OpenAIAdapter
from app.tenancy.context import TenantContext

logger = structlog.get_logger(__name__)


class ModelProviderUnconfiguredError(WolfError):
    """The settings or secrets do not contain a usable model configuration."""

    http_status = 500
    error_code = "model_provider_unconfigured"


async def get_model_for_tenant(
    _ctx: TenantContext,
    settings: Settings,
    secrets: SecretsBackend,
) -> ModelProvider:
    """Return the configured ModelProvider for a request.

    The tenant context is accepted (and reserved) so per-tenant model
    selection can be added without changing the call site.
    """
    provider_name = settings.default_model_provider.lower()
    model_id = settings.default_model_id

    api_key = ""
    if settings.default_model_api_key_ref:
        secret = await secrets.get(settings.default_model_api_key_ref)
        if secret is None:
            raise ModelProviderUnconfiguredError(
                f"Secret {settings.default_model_api_key_ref!r} not found"
            )
        api_key = secret

    match provider_name:
        case "anthropic":
            if not api_key:
                raise ModelProviderUnconfiguredError(
                    "Anthropic provider requires DEFAULT_MODEL_API_KEY_REF"
                )
            return AnthropicAdapter(api_key=api_key, model_id=model_id)
        case "openai":
            if not api_key:
                raise ModelProviderUnconfiguredError(
                    "OpenAI provider requires DEFAULT_MODEL_API_KEY_REF"
                )
            return OpenAIAdapter(
                api_key=api_key,
                model_id=model_id,
                base_url=settings.openai_base_url,
            )
        case "ollama":
            return OllamaAdapter(
                model_id=model_id,
                base_url=settings.ollama_base_url,
            )
        case _:
            raise ModelProviderUnconfiguredError(
                f"Unknown model provider: {provider_name!r}"
            )
