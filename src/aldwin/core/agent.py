"""Agentling agent assembly, skill discovery, session factory, and model switching."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from agentling import Agent, AgentSession, Memory, Model, Skill, Tool

from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.context import build_context
from aldwin.core.home import home_dir, read_base_instructions
from aldwin.core.models import RetryCallback, build_openai_model
from aldwin.core.policy import (
    ApprovalAnswer,
    ApprovalRequest,
    PolicyAskFn,
    PolicySession,
    verify_registered_tools,
    wrap_tools_with_policy,
)
from aldwin.core.settings import (
    AldwinConfig,
    SecretsConfig,
    load_secrets,
    resolve_key,
)
from aldwin.core.skills import build_task as build_task  # noqa: F401  # public re-export
from aldwin.core.workspace import Workspace

logger = logging.getLogger("aldwin.core.agent")

SKILLS_SUBDIR: Final[str] = "skills"
SKILL_FILENAME: Final[str] = "SKILL.md"
DEFAULT_TOOL_TIMEOUT: Final[float] = 60.0
DEFAULT_MAX_TOOL_OUTPUT_CHARS: Final[int] = 10_000

_RESERVED_SKILL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "load_skill",
        "final_answer",
        "web_search",
        "web_fetch",
        "run_command",
        "remember",
        "recall",
        "forget",
        "set_persona",
        "list_dir",
        "glob",
        "grep",
        "read_file",
        "edit_file",
        "write_file",
        "read_document",
        "download_file",
        "todo",
        "calculate",
        "create_skill",
    }
)

AskFn = Callable[[str, str], Awaitable[bool]]
AnyAskFn = AskFn | PolicyAskFn
EmitFn = Callable[[object], Awaitable[None]]


async def _deny_all_ask(_summary: str, _detail: str = "") -> bool:
    """Default fail-closed approval callback when none is supplied."""
    return False


async def _deny_all_policy_ask(_request: ApprovalRequest) -> ApprovalAnswer:
    """Default fail-closed policy approval callback when none is wired."""
    return ApprovalAnswer(answer="no", source="no_approver", reason=None)


def coerce_policy_ask(ask: AnyAskFn | None) -> PolicyAskFn:
    """Adapt a 1-arg `PolicyAskFn` or legacy 2-arg `AskFn` into `PolicyAskFn`."""
    if ask is None:
        return _deny_all_policy_ask

    async def _adapted(request: ApprovalRequest) -> ApprovalAnswer:
        res: object = await ask(request)  # type: ignore[arg-type, call-arg]
        if isinstance(res, ApprovalAnswer):
            return res
        return ApprovalAnswer(
            answer="yes" if bool(res) else "no",
            source="user",
            reason=None,
        )

    import inspect

    try:
        sig = inspect.signature(ask)
        params = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        required_positional = [
            p for p in params if p.default is inspect.Parameter.empty
        ]
        if len(required_positional) >= 2 or len(params) >= 2:
            from typing import cast

            legacy_ask = cast(AskFn, ask)

            async def _legacy_adapted(request: ApprovalRequest) -> ApprovalAnswer:
                approved = await legacy_ask(
                    request.summary,
                    request.detail or "",
                )
                return ApprovalAnswer(
                    answer="yes" if approved else "no",
                    source="user",
                    reason=None,
                )

            return _legacy_adapted
    except (TypeError, ValueError):
        pass

    return _adapted


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Per-session dependencies injected into tools via `build_tools`."""

    workspace: Workspace
    policy_session: PolicySession
    ask: PolicyAskFn = _deny_all_policy_ask
    approval_log: ApprovalLogger | None = None
    emit: EmitFn | None = None
    home: Path | None = None
    secrets: SecretsConfig | None = None
    persona_cap: int = 2000
    env_allow: tuple[str, ...] = ()
    session_id_getter: Callable[[], str] | None = None
    host_flags: dict[str, bool] | None = None
    model_name: str = ""


def build_tools(
    session_ctx: SessionContext,
    *,
    extra_tools: Sequence[Tool] = (),
) -> list[Tool]:
    """Construct built-in tools with `session_ctx` and wrap in policy."""
    import aldwin
    from aldwin.core.tools.calculate import build_calculate_tool
    from aldwin.core.tools.documents import build_document_tools
    from aldwin.core.tools.download import build_download_tool
    from aldwin.core.tools.edits import build_edit_tools
    from aldwin.core.tools.files import build_file_tools
    from aldwin.core.tools.memory_tools import build_memory_tools
    from aldwin.core.tools.persona_tool import build_persona_tool
    from aldwin.core.tools.run_command import build_run_command_tool
    from aldwin.core.tools.skill_tools import build_create_skill_tool
    from aldwin.core.tools.todo import build_todo_tool
    from aldwin.core.tools.web_fetch import build_web_fetch_tool
    from aldwin.core.tools.web_search import build_web_search_tool

    root = (
        session_ctx.home
        if session_ctx.home is not None
        else session_ctx.policy_session.home
    )
    host_flags = session_ctx.host_flags if session_ctx.host_flags is not None else {}

    async def _legacy_ask(summary: str, detail: str = "") -> bool:
        ans = await session_ctx.ask(
            ApprovalRequest(
                tool="unknown",
                tier="ask",
                summary=summary,
                detail=detail,
                grantable=False,
                grant_key=None,
                risky=(),
                digest="",
            )
        )
        return ans.answer in ("yes", "yes_session")

    raw_tools: list[Tool] = [
        build_web_search_tool(home=root, secrets=session_ctx.secrets),
        build_web_fetch_tool(wrapped_by_policy=True),
        build_run_command_tool(
            ask=_legacy_ask,
            cwd=lambda: session_ctx.workspace.root,
            env_allow=session_ctx.env_allow,
            wrapped_by_policy=True,
        ),
        *build_memory_tools(
            home=root,
            session_id_getter=session_ctx.session_id_getter,
        ),
        build_persona_tool(
            ask=_legacy_ask,
            home=root,
            cap=session_ctx.persona_cap,
            wrapped_by_policy=True,
        ),
        *build_file_tools(session_ctx.workspace, session_ctx.policy_session),
        *build_edit_tools(session_ctx.workspace, session_ctx.policy_session),
        *build_document_tools(session_ctx.workspace, session_ctx.policy_session),
        build_download_tool(session_ctx.workspace, session_ctx.policy_session),
        build_todo_tool(),
        build_calculate_tool(),
        build_create_skill_tool(
            home=root,
            host_flags=host_flags,
            version=aldwin.__version__,
            model=session_ctx.model_name or "unknown",
        ),
        *extra_tools,
    ]
    verify_registered_tools(raw_tools)
    return wrap_tools_with_policy(
        raw_tools,
        session=session_ctx.policy_session,
        ask=session_ctx.ask,
        approval_log=session_ctx.approval_log,
    )


@dataclass(frozen=True, slots=True)
class InvalidSkill:
    """A skill folder in `~/.aldwin/skills/` that failed validation."""

    name: str
    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class SkillLoadResult:
    """Valid `Skill` objects and skipped `InvalidSkill` entries from `skills/`."""

    valid: list[Skill]
    invalid: list[InvalidSkill]


def load_base_instructions() -> str:
    """Return the package-shipped base instructions."""
    return read_base_instructions().strip()


def _clean_reason(exc: BaseException) -> str:
    """Format an exception into a single-line human reason for `/skills` and logs."""
    first_line = (
        str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    )
    return " ".join(first_line.split())


def load_skills(skills_dir: Path) -> SkillLoadResult:
    """Load skills from `skills_dir` plus packaged defaults via `build_catalogue`.

    Prefer calling `aldwin.core.skills.build_catalogue` directly in new code.
    """
    import aldwin
    from aldwin.core.skills import build_catalogue, catalogue_skills_for_agent

    home = skills_dir.expanduser().resolve().parent
    catalogue = build_catalogue(
        home,
        version=aldwin.__version__,
        model="unknown",
    )
    valid = catalogue_skills_for_agent(catalogue)
    invalid = [
        InvalidSkill(
            name=entry.name,
            path=entry.path or skills_dir / entry.name,
            reason=next(
                (
                    f.removeprefix("error: ").strip()
                    for f in entry.flags
                    if f.startswith("error:")
                ),
                "invalid",
            ),
        )
        for entry in catalogue
        if entry.skill is None
    ]
    for skill in valid:
        if skill.name in _RESERVED_SKILL_NAMES:
            valid = [s for s in valid if s.name != skill.name]
            invalid.append(
                InvalidSkill(
                    name=skill.name,
                    path=skill.path,
                    reason=f"skill name {skill.name!r} is reserved.",
                )
            )
    return SkillLoadResult(valid=valid, invalid=invalid)


def _resolve_openai_key(
    secrets: SecretsConfig | None,
    home: Path,
) -> str | None:
    """Resolve the OpenAI API key from environment first, then `secrets` or disk."""
    resolved = resolve_key("openai", home)
    if resolved.key:
        return resolved.key
    if secrets is not None:
        direct = secrets.get_secret("openai")
        if direct:
            return direct
    loaded = load_secrets(home)
    return loaded.get_secret("openai")


def build_agent(
    config: AldwinConfig,
    secrets: SecretsConfig | None = None,
    ask: AnyAskFn | None = None,
    *,
    home: Path | None = None,
    workspace: Workspace | Path | None = None,
    policy_session: PolicySession | None = None,
    approval_log: ApprovalLogger | None = None,
    emit: EmitFn | None = None,
    model: Model | None = None,
    tools: Sequence[Tool] | None = None,
    on_retry: RetryCallback | None = None,
    session_id_getter: Callable[[], str] | None = None,
    host_flags: dict[str, bool] | None = None,
) -> Agent:
    """Assemble Aldwin's `Agent` from `config`, `secrets`, and `ask`."""
    import aldwin
    from aldwin.core.skills import build_catalogue, catalogue_skills_for_agent

    root = (home if home is not None else home_dir()).expanduser().resolve()
    flags = host_flags if host_flags is not None else {}

    if isinstance(workspace, Workspace):
        active_ws: Workspace | None = workspace
    elif workspace is not None:
        active_ws = Workspace(workspace, home=root)
    else:
        try:
            active_ws = Workspace(Path.cwd(), home=root)
        except Exception:
            active_ws = None

    if model is not None:
        active_model: Model = model
    else:
        api_key = _resolve_openai_key(secrets, root)
        active_model = build_openai_model(
            config.model,
            api_key=api_key,
            on_retry=on_retry,
        )

    if tools is not None:
        active_tools: tuple[Tool, ...] = tuple(tools)
    else:
        if active_ws is None:
            raise RuntimeError(
                "build_agent requires a workspace when tools are not supplied "
                "(pass workspace= or run from an existing directory)."
            )
        active_policy = policy_session
        if active_policy is None:
            active_policy = PolicySession(workspace=active_ws, home=root)
        ctx = SessionContext(
            workspace=active_ws,
            policy_session=active_policy,
            ask=coerce_policy_ask(ask),
            approval_log=approval_log,
            emit=emit,
            home=root,
            secrets=secrets,
            persona_cap=config.limits.persona_chars,
            env_allow=tuple(config.run_command.env_allow),
            session_id_getter=session_id_getter,
            host_flags=flags,
            model_name=config.model,
        )
        active_tools = tuple(build_tools(ctx))

    catalogue = build_catalogue(
        root,
        version=aldwin.__version__,
        model=config.model,
    )
    skill_list = catalogue_skills_for_agent(catalogue)
    kept_skills: list[Skill] = []
    reserved_invalid: list[InvalidSkill] = []
    for skill in skill_list:
        if skill.name in _RESERVED_SKILL_NAMES:
            reserved_invalid.append(
                InvalidSkill(
                    name=skill.name,
                    path=skill.path,
                    reason=f"skill name {skill.name!r} is reserved.",
                )
            )
        else:
            kept_skills.append(skill)
    skill_list = kept_skills
    invalid = tuple(
        [
            *(
                InvalidSkill(
                    name=entry.name,
                    path=entry.path or root / SKILLS_SUBDIR / entry.name,
                    reason=next(
                        (
                            f.removeprefix("error: ").strip()
                            for f in entry.flags
                            if f.startswith("error:")
                        ),
                        "invalid",
                    ),
                )
                for entry in catalogue
                if entry.skill is None
            ),
            *reserved_invalid,
        ]
    )
    ws_for_context: Callable[[], Path | str] | Path | None = (
        (lambda: active_ws.root) if active_ws is not None else None
    )
    context_manager = build_context(
        root,
        config.limits,
        workspace=ws_for_context,
    )

    agent = Agent(
        model=active_model,
        tools=active_tools,
        skills=skill_list,
        instructions=load_base_instructions(),
        max_steps=config.limits.max_steps,
        tool_timeout=DEFAULT_TOOL_TIMEOUT,
        max_tool_output_chars=DEFAULT_MAX_TOOL_OUTPUT_CHARS,
        context_manager=context_manager,
    )
    setattr(agent, "invalid_skills", invalid)
    setattr(agent, "skill_catalogue", catalogue)
    return agent


def new_session(agent: Agent) -> AgentSession:
    """Create a fresh `AgentSession` from `agent`."""
    return agent.start()


def restore_session(agent: Agent, memory_json: str) -> AgentSession:
    """Create an `AgentSession` and restore its `Memory` from `memory_json`."""
    session = agent.start()
    session.memory = Memory.load_json(memory_json)
    return session


def rebuild_agent(
    config: AldwinConfig,
    secrets: SecretsConfig | None,
    ask: AskFn | None,
    model_name: str,
    *,
    home: Path | None = None,
    model: Model | None = None,
    tools: Sequence[Tool] | None = None,
    on_retry: RetryCallback | None = None,
) -> tuple[Agent, AldwinConfig]:
    """Rebuild the `Agent` for a new `model_name`."""
    cleaned = model_name.strip()
    if not cleaned:
        raise ValueError("model_name must not be empty.")
    updated_config = config.model_copy(update={"model": cleaned})
    agent = build_agent(
        updated_config,
        secrets,
        ask or _deny_all_ask,
        home=home,
        model=model,
        tools=tools,
        on_retry=on_retry,
    )
    return agent, updated_config


def switch_session_model(session: AgentSession, new_agent: Agent) -> AgentSession:
    """Create a session on `new_agent` carrying over `session.memory`."""
    updated_session = new_agent.start()
    updated_session.memory = Memory.from_dict(session.memory.to_dict())
    return updated_session
