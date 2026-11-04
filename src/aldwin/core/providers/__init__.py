"""Model providers and adapters for Aldwin."""

from __future__ import annotations

from .openai_chat import OpenAIChatModel, error_for_response, request_body
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
    "ModelRef",
    "OpenAIChatModel",
    "ProviderEntry",
    "ProviderShape",
    "error_for_response",
    "get_provider",
    "is_loopback_host",
    "list_providers",
    "load_builtin_providers",
    "parse_model_ref",
    "request_body",
    "validate_model_ref",
]
