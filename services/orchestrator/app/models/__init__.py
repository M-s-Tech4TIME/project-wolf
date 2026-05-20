"""Model abstraction layer — provider-agnostic interface and adapters."""

from app.models.anthropic import AnthropicAdapter
from app.models.interface import (
    KNOWN_MODELS,
    ModelProvider,
    default_descriptor_for,
)
from app.models.ollama import OllamaAdapter
from app.models.openai import OpenAIAdapter
from app.models.registry import ToolRegistry, registry

__all__ = [
    "AnthropicAdapter",
    "KNOWN_MODELS",
    "ModelProvider",
    "OllamaAdapter",
    "OpenAIAdapter",
    "ToolRegistry",
    "default_descriptor_for",
    "registry",
]
