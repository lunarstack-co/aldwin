"""Aldwin notepad (`client`): the API every Python door uses to talk to the host."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aldwin.host.in_process import (
    AccessStatus,
    BootstrapResult,
    ClientBootstrapError,
    ConnectorCommandError,
    InvalidSkillSummary,
    KeyCheckResult,
    LocalProviderStatus,
    MemorySummary,
    ModelInfo,
    NoteEntry,
    OnboardingStatus,
    PersonaSummary,
    PlatformNotSupportedError,
    SearchSwitchResult,
    SkillsReport,
    SkillSummary,
    ToolInfo,
    WorkspaceError,
    WorkspaceNotAllowedError,
    WorkspaceNotFoundError,
    WorkspaceSummary,
    active_search_provider_name,
    bootstrap_host,
    check_openai_key,
    connector_needs_approval,
    connectors_add_command,
    connectors_add_local_command,
    connectors_enable_command,
    connectors_find_configs,
    connectors_import_command,
    connectors_login_command,
    connectors_logout_command,
    connectors_remove_command,
    connectors_set_enabled,
    connectors_summary,
    describe_active_search,
    detect_local_providers,
    get_builtin_tools,
    get_memory_summary,
    get_onboarding_status,
    get_persona_summary,
    get_persona_text,
    get_skills_report,
    list_models_for,
    mark_whats_new_seen,
    mask_secret,
    open_in_process_client,
    pop_profile_truncation_warning,
    purge_home_data,
    resolve_workspace_summary,
    sanitize_display_text,
    save_provider_key,
    save_setup,
    switch_search_provider,
    switchable_search_providers,
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
    "AccessStatus",
    "AldwinClient",
    "BootstrapResult",
    "ClientBootstrapError",
    "ConnectorCommandError",
    "InvalidSkillSummary",
    "KeyCheckResult",
    "LocalProviderStatus",
    "MemorySummary",
    "ModelInfo",
    "NoteEntry",
    "OnboardingStatus",
    "PersonaSummary",
    "PlatformNotSupportedError",
    "ProtocolError",
    "SearchSwitchResult",
    "SkillSummary",
    "SkillsReport",
    "ToolInfo",
    "WorkspaceError",
    "WorkspaceNotAllowedError",
    "WorkspaceNotFoundError",
    "WorkspaceSummary",
    "active_search_provider_name",
    "bootstrap",
    "check_openai_key",
    "connector_needs_approval",
    "connectors_add_command",
    "connectors_add_local_command",
    "connectors_enable_command",
    "connectors_find_configs",
    "connectors_import_command",
    "connectors_login_command",
    "connectors_logout_command",
    "connectors_remove_command",
    "connectors_set_enabled",
    "connectors_summary",
    "describe_active_search",
    "detect_local_providers",
    "verify_active_openai_key",
    "get_builtin_tools",
    "get_memory_summary",
    "get_onboarding_status",
    "get_persona_summary",
    "get_persona_text",
    "get_skills_report",
    "list_models_for",
    "mark_whats_new_seen",
    "mask_secret",
    "open_in_process_client",
    "pop_profile_truncation_warning",
    "purge_home_data",
    "resolve_workspace_summary",
    "sanitize_display_text",
    "save_provider_key",
    "save_setup",
    "switch_search_provider",
    "switchable_search_providers",
    "update_available_nudge",
    "whats_new_due",
]
