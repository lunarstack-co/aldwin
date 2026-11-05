"""Model providers, backends, and adapters for Aldwin."""

from __future__ import annotations

from .anthropic import AnthropicBackend, AnthropicMessagesModel
from .base import AccessStatus, ModelInfo, ProviderBackend
from .discovery import (
    LocalInventoryCache,
    LocalProviderStatus,
    detect_local_providers,
)
from .factory import get_backend, resolve_provider_key
from .keys import resolve_provider_secret_field, save_provider_key
from .ollama import OllamaBackend, OllamaModel, parse_ollama_show_response
from .openai_chat import (
    OpenAIChatBackend,
    OpenAIChatModel,
    error_for_response,
    request_body,
)
from .registry import (
    ModelRef,
    ProviderEntry,
    ProviderShape,
    get_provider,
    is_loopback_host,
    list_providers,
    load_builtin_providers,
    parse_model_ref,
    validate_model_ref,
)

__all__ = [
    "AccessStatus",
    "AnthropicBackend",
    "AnthropicMessagesModel",
    "LocalInventoryCache",
    "LocalProviderStatus",
    "ModelInfo",
    "ModelRef",
    "OllamaBackend",
    "OllamaModel",
    "OpenAIChatBackend",
    "OpenAIChatModel",
    "ProviderBackend",
    "ProviderEntry",
    "ProviderShape",
    "detect_local_providers",
    "error_for_response",
    "get_backend",
    "get_provider",
    "is_loopback_host",
    "list_providers",
    "load_builtin_providers",
    "parse_model_ref",
    "parse_ollama_show_response",
    "request_body",
    "resolve_provider_key",
    "resolve_provider_secret_field",
    "save_provider_key",
    "validate_model_ref",
]
