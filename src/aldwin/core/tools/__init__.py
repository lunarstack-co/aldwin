"""Built-in Agentling tools, tool summary contract, and registry for Aldwin."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from agentling import Tool

    from aldwin.core.settings import SecretsConfig
    from aldwin.core.tools.memory_tools import (
        ForgetTool,
        RecallTool,
        RememberTool,
        build_memory_tools,
    )
    from aldwin.core.tools.persona_tool import (
        SetPersonaTool,
        build_persona_tool,
    )
    from aldwin.core.tools.web_search import SearchProvider

AskFn = Callable[[str, str], Awaitable[bool]]

MAX_SUMMARY_CHARS: Final[int] = 120

BUILTIN_TOOL_NAMES: Final[tuple[str, ...]] = (
    "web_search",
    "web_fetch",
    "run_command",
    "remember",
    "recall",
    "forget",
    "set_persona",
)


class ToolOutput(str):
    """String return value for tools that also carries card metadata (`summary`, `ok`).

    Because `ToolOutput` subclasses `str` and returns `self` from `__str__()`,
    Agentling stores the full string in session memory while preserving
    `.summary` and `.ok` on `ToolResult.content` for `AldwinHost`.
    """

    summary: str
    ok: bool
    duration_ms: int | None

    def __new__(
        cls,
        content: str = "",
        *,
        summary: str = "",
        ok: bool = True,
        duration_ms: int | None = None,
    ) -> ToolOutput:
        instance = super().__new__(cls, content)
        instance.summary = summary
        instance.ok = ok
        instance.duration_ms = duration_ms
        return instance

    def __str__(self) -> str:
        return self

    def __deepcopy__(self, _memo: object) -> ToolOutput:
        return ToolOutput(
            super().__str__(),
            summary=getattr(self, "summary", ""),
            ok=getattr(self, "ok", True),
            duration_ms=getattr(self, "duration_ms", None),
        )

    def __add__(self, other: str) -> ToolOutput:
        return ToolOutput(
            super().__add__(other),
            summary=self.summary,
            ok=self.ok,
            duration_ms=self.duration_ms,
        )


ToolOutcome = ToolOutput


@dataclass(frozen=True, slots=True)
class ToolInfo:
    """One-line descriptor for `/tools` in the CLI."""

    name: str
    description: str
    requires_approval: bool
    approval_note: str = ""


# `/tools` notes; `test_builtin_tools` checks each matches `policy.REGISTRY`.
APPROVAL_NOTE_ASK: Final[str] = "always asks y/n"
APPROVAL_NOTE_DIFF: Final[str] = "asks every time"
APPROVAL_NOTE_DIFF_GRANTABLE: Final[str] = "a = allow this file for the session"
APPROVAL_NOTE_REMEMBER: Final[str] = "replace= shows a diff"


_BUILTIN_TOOL_CATALOG: Final[tuple[ToolInfo, ...]] = (
    ToolInfo(
        name="web_search",
        description="Search the web with Tavily and return top numbered results.",
        requires_approval=False,
    ),
    ToolInfo(
        name="web_fetch",
        description="Fetch a web page as readable text (up to 20,000 characters).",
        requires_approval=False,
    ),
    ToolInfo(
        name="run_command",
        description="Run a shell command in the workspace after y/n approval.",
        requires_approval=True,
        approval_note=APPROVAL_NOTE_ASK,
    ),
    ToolInfo(
        name="remember",
        description=(
            "Save a short durable fact, or update one in place with replace=."
        ),
        requires_approval=False,
        approval_note=APPROVAL_NOTE_REMEMBER,
    ),
    ToolInfo(
        name="recall",
        description="Search memory notes and past conversations (ranked).",
        requires_approval=False,
    ),
    ToolInfo(
        name="forget",
        description="Delete a saved memory note after diff approval.",
        requires_approval=True,
        approval_note=APPROVAL_NOTE_DIFF,
    ),
    ToolInfo(
        name="set_persona",
        description="Replace Aldwin's persona.md after y/n approval.",
        requires_approval=True,
        approval_note=APPROVAL_NOTE_DIFF,
    ),
    ToolInfo(
        name="list_dir",
        description="List files and folders in the workspace.",
        requires_approval=False,
    ),
    ToolInfo(
        name="glob",
        description="Find files by name with a glob pattern.",
        requires_approval=False,
    ),
    ToolInfo(
        name="grep",
        description="Search file contents for a regular expression.",
        requires_approval=False,
    ),
    ToolInfo(
        name="read_file",
        description="Read a text file and return numbered lines.",
        requires_approval=False,
    ),
    ToolInfo(
        name="edit_file",
        description="Replace a unique exact string in a file after diff approval.",
        requires_approval=True,
        approval_note=APPROVAL_NOTE_DIFF_GRANTABLE,
    ),
    ToolInfo(
        name="write_file",
        description="Create or overwrite a text file after diff approval.",
        requires_approval=True,
        approval_note=APPROVAL_NOTE_DIFF_GRANTABLE,
    ),
    ToolInfo(
        name="read_document",
        description=(
            "Read a PDF, Word, PowerPoint, Excel, CSV/TSV, or HTML file as markdown."
        ),
        requires_approval=False,
    ),
    ToolInfo(
        name="download_file",
        description="Download a public URL into the workspace after y/n approval.",
        requires_approval=True,
        approval_note=APPROVAL_NOTE_ASK,
    ),
    ToolInfo(
        name="todo",
        description="Keep a checklist for multi-step jobs (3+ steps).",
        requires_approval=False,
    ),
    ToolInfo(
        name="calculate",
        description="Exact arithmetic and ISO date maths (no shell).",
        requires_approval=False,
    ),
    ToolInfo(
        name="create_skill",
        description="Create or update a user skill after diff approval.",
        requires_approval=True,
        approval_note=APPROVAL_NOTE_DIFF,
    ),
)


def _shorten_summary(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Collapse whitespace to a single line and cap at `limit` characters."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    if limit <= 1:
        return collapsed[:limit]
    return f"{collapsed[: limit - 1]}…"


def summarize_tool_result(
    name: str,
    content: str,
    *,
    is_error: bool = False,
) -> tuple[bool, str]:
    """Return `(ok, one_line_summary)` for a completed tool call."""
    del name
    if isinstance(content, ToolOutput):
        ok = False if is_error else content.ok
        return ok, _shorten_summary(content.summary)

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Never surface the untrusted wrapper as the CLI row summary.
        if stripped.startswith("<untrusted ") or stripped.startswith("</untrusted"):
            continue
        if stripped.startswith("ToolCallError: "):
            stripped = stripped.removeprefix("ToolCallError: ")
        return (not is_error), _shorten_summary(stripped)
    return (not is_error), ""


def builtin_tools(
    ask: AskFn,
    home: Path | None = None,
    secrets: SecretsConfig | None = None,
    *,
    search_provider: SearchProvider | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    persona_cap: int | None = None,
    session_id_getter: Callable[[], str] | None = None,
) -> list[Tool]:
    """Return Aldwin's built-in tools, in `BUILTIN_TOOL_NAMES` order."""
    from aldwin.core.persona import DEFAULT_PERSONA_CAP
    from aldwin.core.tools.memory_tools import build_memory_tools
    from aldwin.core.tools.persona_tool import build_persona_tool
    from aldwin.core.tools.run_command import build_run_command_tool
    from aldwin.core.tools.web_fetch import build_web_fetch_tool
    from aldwin.core.tools.web_search import build_web_search_tool

    web_search_tool = build_web_search_tool(
        home=home,
        secrets=secrets,
        provider=search_provider,
    )
    web_fetch_tool = build_web_fetch_tool()
    run_command_tool = build_run_command_tool(
        ask=ask,
        cwd=cwd,
        env=env,
    )
    remember_tool, recall_tool, forget_tool = build_memory_tools(
        home=home,
        session_id_getter=session_id_getter,
    )
    persona_tool = build_persona_tool(
        ask=ask,
        home=home,
        cap=persona_cap if persona_cap is not None else DEFAULT_PERSONA_CAP,
    )
    return [
        web_search_tool,
        web_fetch_tool,
        run_command_tool,
        remember_tool,
        recall_tool,
        forget_tool,
        persona_tool,
    ]


def list_builtin_tools() -> list[ToolInfo]:
    """Return one-line descriptions of the built-in tools for `/tools`."""
    return list(_BUILTIN_TOOL_CATALOG)


def __getattr__(name: str) -> Any:
    """Lazily import tool classes so `aldwin --version` avoids loading `agentling`."""
    if name in ("ForgetTool", "RecallTool", "RememberTool", "build_memory_tools"):
        from aldwin.core.tools import memory_tools

        return getattr(memory_tools, name)
    if name in ("SetPersonaTool", "build_persona_tool"):
        from aldwin.core.tools import persona_tool

        return getattr(persona_tool, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AskFn",
    "BUILTIN_TOOL_NAMES",
    "ForgetTool",
    "RecallTool",
    "RememberTool",
    "SetPersonaTool",
    "ToolInfo",
    "ToolOutcome",
    "ToolOutput",
    "build_memory_tools",
    "build_persona_tool",
    "builtin_tools",
    "list_builtin_tools",
    "summarize_tool_result",
]
