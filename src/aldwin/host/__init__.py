"""Aldwin pass (`host`): receives door requests, runs turns, and manages sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aldwin.host.in_process import (
    BootstrapResult,
    ClientBootstrapError,
    ClientTransport,
    InProcessTransport,
    InvalidSkillSummary,
    KeyCheckResult,
    MemorySummary,
    NewConversationHandler,
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
    get_builtin_tools,
    get_memory_summary,
    get_onboarding_status,
    get_persona_summary,
    get_persona_text,
    get_skills_report,
    list_available_models,
    mask_secret,
    open_in_process_client,
    pop_profile_truncation_warning,
    purge_home_data,
    resolve_workspace_summary,
    sanitize_display_text,
    save_setup,
    update_available_nudge,
    verify_active_openai_key,
)

if TYPE_CHECKING:
    from aldwin.host.runner import (
        AgentBuilder,
        AldwinHost,
        ApprovalBroker,
        AskFn,
        RequestScope,
        classify_turn_error,
        format_args_preview,
        format_tool_summary,
    )
    from aldwin.host.sessions import (
        SessionMeta,
        SessionStore,
        StoredSession,
        derive_session_title,
        shorten_one_line,
    )

_RUNNER_EXPORTS = {
    "AgentBuilder",
    "AldwinHost",
    "ApprovalBroker",
    "AskFn",
    "RequestScope",
    "classify_turn_error",
    "format_args_preview",
    "format_tool_summary",
}

_SESSIONS_EXPORTS = {
    "SessionMeta",
    "SessionStore",
    "StoredSession",
    "derive_session_title",
    "shorten_one_line",
}


def __getattr__(name: str) -> Any:
    """Lazily import `runner` and `sessions` on attribute access."""
    if name in _RUNNER_EXPORTS:
        from aldwin.host import runner

        return getattr(runner, name)
    if name in _SESSIONS_EXPORTS:
        from aldwin.host import sessions

        return getattr(sessions, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgentBuilder",
    "AldwinHost",
    "ApprovalBroker",
    "AskFn",
    "BootstrapResult",
    "ClientBootstrapError",
    "ClientTransport",
    "InProcessTransport",
    "InvalidSkillSummary",
    "KeyCheckResult",
    "MemorySummary",
    "NewConversationHandler",
    "NoteEntry",
    "OnboardingStatus",
    "PersonaSummary",
    "PlatformNotSupportedError",
    "RequestScope",
    "SessionMeta",
    "SessionStore",
    "SkillSummary",
    "SkillsReport",
    "StoredSession",
    "ToolInfo",
    "WorkspaceError",
    "WorkspaceNotAllowedError",
    "WorkspaceNotFoundError",
    "WorkspaceSummary",
    "bootstrap_host",
    "check_openai_key",
    "verify_active_openai_key",
    "classify_turn_error",
    "derive_session_title",
    "format_args_preview",
    "format_tool_summary",
    "get_builtin_tools",
    "get_memory_summary",
    "get_onboarding_status",
    "get_persona_summary",
    "get_persona_text",
    "get_skills_report",
    "list_available_models",
    "mask_secret",
    "open_in_process_client",
    "pop_profile_truncation_warning",
    "purge_home_data",
    "resolve_workspace_summary",
    "sanitize_display_text",
    "save_setup",
    "shorten_one_line",
    "update_available_nudge",
]
