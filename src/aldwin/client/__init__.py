"""Aldwin notepad (`client`): the API every Python door uses to talk to the host."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aldwin.host.in_process import (
    BootstrapResult,
    ClientBootstrapError,
    InvalidSkillSummary,
    KeyCheckResult,
    MemorySummary,
    NoteEntry,
    OnboardingStatus,
    PersonaSummary,
    PlatformNotSupportedError,
    SkillsReport,
    SkillSummary,
    ToolInfo,
    WorkspaceError,
    WorkspaceNotAllowedError,
    WorkspaceNotFoundError,
    WorkspaceSummary,
    bootstrap_host,
    check_openai_key,
    format_version_string,
    get_builtin_tools,
    get_memory_summary,
    get_onboarding_status,
    get_persona_summary,
    get_persona_text,
    get_skills_report,
    list_available_models,
    mark_whats_new_seen,
    mask_secret,
    open_in_process_client,
    pop_profile_truncation_warning,
    purge_home_data,
    resolve_workspace_summary,
    sanitize_display_text,
    save_setup,
    update_available_nudge,
    verify_active_openai_key,
    whats_new_due,
)

if TYPE_CHECKING:
    from aldwin.client.client import AldwinClient, ProtocolError


def bootstrap(
    *,
    plain: bool = False,
    debug: bool = False,
    workspace: Path | str | None = None,
) -> BootstrapResult:
    """Initialise the Aldwin host and return startup metadata for the door."""
    return bootstrap_host(plain=plain, debug=debug, workspace=workspace)


def __getattr__(name: str) -> Any:
    """Lazily import `AldwinClient` and `ProtocolError` when accessed."""
    if name in ("AldwinClient", "ProtocolError"):
        from aldwin.client import client

        return getattr(client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AldwinClient",
    "BootstrapResult",
    "ClientBootstrapError",
    "InvalidSkillSummary",
    "KeyCheckResult",
    "MemorySummary",
    "NoteEntry",
    "OnboardingStatus",
    "PersonaSummary",
    "PlatformNotSupportedError",
    "ProtocolError",
    "SkillSummary",
    "SkillsReport",
    "ToolInfo",
    "WorkspaceError",
    "WorkspaceNotAllowedError",
    "WorkspaceNotFoundError",
    "WorkspaceSummary",
    "bootstrap",
    "check_openai_key",
    "format_version_string",
    "verify_active_openai_key",
    "get_builtin_tools",
    "get_memory_summary",
    "get_onboarding_status",
    "get_persona_summary",
    "get_persona_text",
    "get_skills_report",
    "list_available_models",
    "mark_whats_new_seen",
    "mask_secret",
    "open_in_process_client",
    "pop_profile_truncation_warning",
    "purge_home_data",
    "resolve_workspace_summary",
    "sanitize_display_text",
    "save_setup",
    "update_available_nudge",
    "whats_new_due",
]
