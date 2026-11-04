"""ProviderBackend protocol and model metadata data structures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentling import Model


class AccessStatus(StrEnum):
    """Result of checking network and credential access to a provider."""

    OK = "ok"
    INVALID_KEY = "invalid_key"
    NO_ACCESS = "no_access"
    UNREACHABLE = "unreachable"
    NOT_RUNNING = "not_running"


@dataclass(frozen=True)
class ModelInfo:
    """Metadata describing one model available from a provider."""

    id: str
    title: str
    context_window: int
    supports_tools: bool
    local: bool


@runtime_checkable
class ProviderBackend(Protocol):
    """Protocol for provider lifecycle, listing, and model construction."""

    async def list_models(self) -> list[ModelInfo]:
        """Fetch available models from the provider endpoint."""
        ...

    async def check_access(self) -> AccessStatus:
        """Verify reachability and credential validity."""
        ...

    def build_model(self, model_id: str) -> Model:
        """Instantiate an Agentling Model adapter for `model_id`."""
        ...

    async def describe_model(self, model_id: str) -> ModelInfo | None:
        """Fetch or compute detailed metadata for `model_id`."""
        ...
