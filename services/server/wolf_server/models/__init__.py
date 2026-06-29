"""Model abstraction layer — provider-agnostic interface and adapters."""

from wolf_server.models.anthropic import AnthropicAdapter
from wolf_server.models.interface import (
    KNOWN_MODELS,
    ModelProvider,
    default_descriptor_for,
)
from wolf_server.models.ollama import OllamaAdapter
from wolf_server.models.openai import OpenAIAdapter
from wolf_server.models.openrouter import OpenRouterAdapter
from wolf_server.models.registry import ToolRegistry, registry

__all__ = [
    "AnthropicAdapter",
    "KNOWN_MODELS",
    "ModelProvider",
    "OllamaAdapter",
    "OpenAIAdapter",
    "OpenRouterAdapter",
    "ToolRegistry",
    "default_descriptor_for",
    "registry",
]
