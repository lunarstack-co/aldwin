"""Structured tool display payloads and context-usage models for protocol v2."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

TERMINAL_MAX_LINES: Final[int] = 400
TERMINAL_MAX_BYTES: Final[int] = 65_536
DIFF_MAX_LINES: Final[int] = 600
FILES_MAX: Final[int] = 300
SEARCH_MAX: Final[int] = 10
TABLE_MAX_ROWS: Final[int] = 200
TABLE_MAX_COLS: Final[int] = 20

DisplayKind = Literal[
    "terminal",
    "diff",
    "files",
    "search",
    "page",
    "table",
    "memory",
    "denied",
    "grant",
]
DiffLineKind = Literal["context", "added", "removed"]
FileEntryKind = Literal["file", "folder"]
MemoryChangeKind = Literal["added", "replaced", "removed"]
DisplayGrantScope = Literal["file", "folder", "tool"]


class _DisplayModel(BaseModel):
    """Shared config for all display models so unknown fields are ignored."""

    model_config = ConfigDict(extra="ignore")


class TerminalDisplay(_DisplayModel):
    """Command execution preview or result (`run_command`)."""

    kind: Literal["terminal"] = "terminal"
    command: str
    cwd: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: int = Field(default=0, ge=0)
    truncated: bool = False


class DiffLine(_DisplayModel):
    """One context, added, or removed line in a `DiffDisplay`."""

    kind: DiffLineKind
    text: str


class DiffDisplay(_DisplayModel):
    """Unified diff preview or result (`edit_file`, `write_file`, Diff tools)."""

    kind: Literal["diff"] = "diff"
    path: str
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    lines: list[DiffLine] = Field(default_factory=list, max_length=DIFF_MAX_LINES)
    truncated: bool = False


class FileEntry(_DisplayModel):
    """One file or directory entry in a `FilesDisplay`."""

    path: str
    kind: FileEntryKind
    additions: int | None = Field(default=None, ge=0)
    deletions: int | None = Field(default=None, ge=0)


class FilesDisplay(_DisplayModel):
    """Directory listing or glob result (`list_dir`, `glob`)."""

    kind: Literal["files"] = "files"
    root: str
    entries: list[FileEntry] = Field(default_factory=list, max_length=FILES_MAX)
    truncated: bool = False


class SearchHit(_DisplayModel):
    """One web search hit in a `SearchDisplay`."""

    title: str
    url: str
    snippet: str = ""


class SearchDisplay(_DisplayModel):
    """Web search result list (`web_search`)."""

    kind: Literal["search"] = "search"
    query: str
    results: list[SearchHit] = Field(default_factory=list, max_length=SEARCH_MAX)
    truncated: bool = False


class PageDisplay(_DisplayModel):
    """Fetched web page summary (`web_fetch`)."""

    kind: Literal["page"] = "page"
    url: str
    title: str = ""
    site: str = ""
    description: str = ""


class TableColumn(_DisplayModel):
    """Column header descriptor in a `TableDisplay`."""

    key: str
    label: str


class TableDisplay(_DisplayModel):
    """Tabular document preview (`read_document` on CSV and spreadsheets)."""

    kind: Literal["table"] = "table"
    columns: list[TableColumn] = Field(default_factory=list, max_length=TABLE_MAX_COLS)
    rows: list[dict[str, str]] = Field(default_factory=list, max_length=TABLE_MAX_ROWS)
    truncated: bool = False


class MemoryChange(_DisplayModel):
    """One added, replaced, or removed memory fact."""

    text: str
    change: MemoryChangeKind


class MemoryDisplay(_DisplayModel):
    """Memory note updates (`remember`, `forget`)."""

    kind: Literal["memory"] = "memory"
    changes: list[MemoryChange] = Field(default_factory=list)


class DeniedDisplay(_DisplayModel):
    """Policy or user refusal notice."""

    kind: Literal["denied"] = "denied"
    rule: str
    message: str


class GrantDisplay(_DisplayModel):
    """Permission grant scope and reach on a grantable approval."""

    kind: Literal["grant"] = "grant"
    scope: DisplayGrantScope
    reach: list[str] = Field(default_factory=list)


Display = Annotated[
    TerminalDisplay
    | DiffDisplay
    | FilesDisplay
    | SearchDisplay
    | PageDisplay
    | TableDisplay
    | MemoryDisplay
    | DeniedDisplay
    | GrantDisplay,
    Field(discriminator="kind"),
]


class ContextSegment(_DisplayModel):
    """Estimated token count for one prompt segment."""

    key: str
    label: str
    tokens: int = Field(ge=0)


class ContextUsage(_DisplayModel):
    """Model context window limit, used input tokens, and per-segment breakdown."""

    limit: int = Field(ge=1)
    used: int = Field(ge=0)
    segments: list[ContextSegment] = Field(default_factory=list)


@lru_cache(maxsize=1)
def _display_adapter() -> TypeAdapter[Display]:
    return TypeAdapter(Display)


def validate_display(raw: Any) -> Display:
    """Validate a Python dict or model into a typed `Display` instance."""
    return _display_adapter().validate_python(raw)
