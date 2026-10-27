"""Slash command completer and command handlers for Aldwin's chat screen."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Final, Protocol

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.input import Input
from rich.table import Table
from rich.text import Text

from aldwin.cli.render import ChatRenderer
from aldwin.cli.theme import filter_canonical_models
from aldwin.client import (
    MemorySummary,
    PersonaSummary,
    ProtocolError,
    SkillsReport,
    ToolInfo,
    WorkspaceError,
    WorkspaceSummary,
)
from aldwin.protocol import DoneEvent, ErrorEvent, HistoryEvent, TodoUpdatedEvent

HISTORY_FETCH_LIMIT: Final[int] = 500

SLASH_COMMANDS: Final[tuple[tuple[str, str], ...]] = (
    ("/help", "Show commands and keyboard shortcuts"),
    ("/new", "Start a new conversation"),
    ("/history", "Show this conversation’s saved messages"),
    ("/model", "Switch the OpenAI chat model"),
    ("/persona", "Show current persona and file path"),
    ("/memory", "Show profile, recent notes, and index status"),
    ("/skills", "List skills · /skills show <name> for the body"),
    ("/todo", "Show the current task checklist"),
    ("/workspace", "Show or switch the workspace folder"),
    ("/verbose", "Show or hide file previews for this session"),
    ("/tools", "List built-in tools (includes recall, remember, forget)"),
    ("/exit", "Save and exit Aldwin"),
)

KEYBINDINGS_HELP: Final[tuple[tuple[str, str], ...]] = (
    ("Enter", "Send message (queues while Aldwin is working)"),
    ("Alt+Enter / Shift+Enter / Ctrl+J", "Insert a new line"),
    ("@", "Open the skill picker (then Tab to insert)"),
    ("Esc", "Stop answer, close overlay, or clear draft (double-Esc)"),
    ("?", "Shortcuts in the footer (when prompt is empty)"),
    ("↑ / ↓", "History, or move in slash / approval lists"),
    ("Ctrl+R", "Search input history"),
    ("Ctrl+C", "Clear draft; twice on empty prompt saves and exits"),
    ("Ctrl+D", "Save and exit"),
)


class SlashCommandClient(Protocol):
    """Structural protocol satisfied by `AldwinClient` and test doubles."""

    @property
    def model(self) -> str: ...

    @property
    def updated_at(self) -> str | None: ...

    async def new_conversation(self) -> None: ...

    async def history(self, limit: int = 20) -> HistoryEvent: ...

    async def list_models(self) -> list[str]: ...

    async def set_model(self, name: str) -> DoneEvent | ErrorEvent: ...

    def persona_summary(self) -> PersonaSummary: ...

    def memory_summary(self) -> MemorySummary: ...

    def skills_report(self) -> SkillsReport: ...

    def tools_summary(self) -> list[ToolInfo]: ...

    def workspace_summary(self) -> WorkspaceSummary: ...

    def set_workspace(self, new_root: Path | str) -> WorkspaceSummary: ...

    async def todo_get(self) -> TodoUpdatedEvent: ...


class SlashCompleter(Completer):
    """Prompt-toolkit completer for `/help`, `/new`, `/model`, etc.."""

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        _ = complete_event
        before = document.text_before_cursor.lstrip()
        if not before.startswith("/") or " " in before or "\n" in before:
            return

        prefix = before.lower()
        for cmd, description in SLASH_COMMANDS:
            if cmd.startswith(prefix):
                yield Completion(
                    text=cmd,
                    start_position=-len(before),
                    display=cmd,
                    display_meta=description,
                )


def render_shortcuts(renderer: ChatRenderer) -> None:
    """Print the keyboard shortcuts table (`?` shortcut and `/help`)."""
    console = renderer.console
    console.print(Text("Keys:", style="accent"))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="fg", no_wrap=True)
    grid.add_column(style="muted")
    for key, desc in KEYBINDINGS_HELP:
        grid.add_row(f"  {key}", desc)
    console.print(grid)


def _render_help(renderer: ChatRenderer) -> None:
    console = renderer.console
    console.print(Text("Commands:", style="accent"))
    cmd_grid = Table.grid(padding=(0, 2))
    cmd_grid.add_column(style="fg", no_wrap=True)
    cmd_grid.add_column(style="muted")
    for cmd, desc in SLASH_COMMANDS:
        cmd_grid.add_row(f"  {cmd}", desc)
    console.print(cmd_grid)
    console.print()
    render_shortcuts(renderer)


async def _handle_model_command(
    arg: str,
    *,
    client: SlashCommandClient,
    renderer: ChatRenderer,
    prompt_line: Callable[[str], Awaitable[str]],
    input_dev: Input | None = None,
) -> None:
    from aldwin.cli.picker import pick_index

    console = renderer.console
    show_all = arg.lower() == "all"
    if arg and not show_all:
        res = await client.set_model(arg)
        if isinstance(res, ErrorEvent):
            renderer.render_error(res.code, res.message)
        else:
            console.print(Text(f"✓ Switched model to {arg}.", style="ok"))
        return

    all_models = await client.list_models()
    if not all_models:
        all_models = [client.model]

    if show_all:
        models = list(all_models)
        hidden_count = 0
    else:
        models, hidden_count = filter_canonical_models(all_models, client.model)

    header = "Available OpenAI chat models:"
    if hidden_count > 0:
        header += (
            f" (showing {len(models)} canonical · "
            f"use /model all for all {len(all_models)})"
        )
    console.print(Text(header, style="muted"))

    labels = [f"{name} (current)" if name == client.model else name for name in models]
    default_idx = 0
    if client.model in models:
        default_idx = models.index(client.model)

    use_picker = (
        isinstance(input_dev, Input) and console.is_interactive and not renderer.plain
    )
    if use_picker:
        chosen_idx = await pick_index(
            labels,
            default=default_idx,
            input_dev=input_dev,
            plain=renderer.plain,
        )
        if chosen_idx is None:
            console.print(Text(f"Model unchanged ({client.model}).", style="muted"))
            return
        chosen = models[chosen_idx]
    else:
        for idx, name in enumerate(models, start=1):
            marker = " (current)" if name == client.model else ""
            style = "ok" if name == client.model else ""
            console.print(Text(f"  {idx}. {name}{marker}", style=style))
        try:
            raw_choice = await prompt_line(
                f"Choose model [1-{len(models)}, or Enter to cancel]: "
            )
        except (EOFError, KeyboardInterrupt):
            raw_choice = ""

        choice = raw_choice.strip()
        if not choice:
            console.print(Text(f"Model unchanged ({client.model}).", style="muted"))
            return

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(models):
                chosen = models[idx - 1]
            else:
                console.print(
                    Text(
                        f"Invalid selection {choice!r}. "
                        f"Enter a number from 1 to {len(models)}.",
                        style="err",
                    )
                )
                return
        else:
            chosen = choice

    res = await client.set_model(chosen)
    if isinstance(res, ErrorEvent):
        renderer.render_error(res.code, res.message)
    else:
        console.print(Text(f"✓ Switched model to {chosen}.", style="ok"))


def _render_persona(client: SlashCommandClient, renderer: ChatRenderer) -> None:
    console = renderer.console
    info = client.persona_summary()
    console.print(
        Text(
            f"Persona ({info.display_path} · "
            f"{info.size_chars}/{info.cap_chars} chars):",
            style="muted",
        ),
        soft_wrap=True,
    )
    console.print(Text(info.text, style="fg"))


def _render_memory(client: SlashCommandClient, renderer: ChatRenderer) -> None:
    console = renderer.console
    mem = client.memory_summary()
    console.print(
        Text(
            f"Profile: {mem.display_profile_path} "
            f"({mem.profile_size_chars}/{mem.profile_cap_chars} chars)",
            style="muted",
        ),
        soft_wrap=True,
    )
    if mem.profile_truncated:
        console.print(
            Text(
                f"Your profile is over {mem.profile_cap_chars:,} characters; "
                "only the first part is used",
                style="warn",
            )
        )
    window = "day" if mem.notes_window_days == 1 else f"{mem.notes_window_days} days"
    console.print(
        Text(
            f"Notes in the last {window}: {mem.notes_in_window}",
            style="muted",
        )
    )
    if mem.recent_notes:
        for note in mem.recent_notes:
            console.print(Text(f"  - [{note.date}] {note.text}", style="fg"))
    else:
        console.print(Text("  (no recent notes)", style="muted"))

    size_label = getattr(mem, "index_size_label", None) or "0 B"
    console.print(
        Text(
            f"{getattr(mem, 'indexed_notes', 0)} notes · "
            f"{getattr(mem, 'indexed_conversations', 0)} conversations indexed · "
            f"index {size_label}",
            style="muted",
        )
    )


SKILLS_FOOTER: Final[str] = (
    "Add or edit skills in ~/.aldwin/skills/ · delete a user copy to restore "
    "the default · type @ to pick one"
)


def render_skills_report(report: SkillsReport, console: object) -> None:
    """Print `/skills` rows: `name · source · description` (and error rows)."""
    from rich.console import Console

    rich_console = console if isinstance(console, Console) else Console()
    if not report.valid:
        rich_console.print(Text("  (no skills)", style="muted"))
    for skill in report.valid:
        from aldwin.client import sanitize_display_text

        error = getattr(skill, "error", None)
        if error:
            name = sanitize_display_text(skill.name)
            reason = sanitize_display_text(str(error))
            rich_console.print(Text(f"{name} · error: {reason}", style="err"))
            continue

        name = sanitize_display_text(skill.name)
        desc = sanitize_display_text(skill.description)
        if len(desc) > 80:
            desc = desc[:79] + "…"
        source = sanitize_display_text(skill.source)
        flags = getattr(skill, "flags", ())
        extra = [
            sanitize_display_text(str(f))
            for f in flags
            if f != "tools removed" and not str(f).startswith("error:")
        ]
        if getattr(skill, "tools_removed", False) or "tools removed" in flags:
            source = f"{source} · tools removed"
        if extra:
            source = f"{source} · " + " · ".join(extra)
        rich_console.print(Text(f"{name} · {source} · {desc}", style="fg"))
    rich_console.print(Text(SKILLS_FOOTER, style="muted"))


def _render_skills(client: SlashCommandClient, renderer: ChatRenderer) -> None:
    report = client.skills_report()
    render_skills_report(report, renderer.console)


def _render_workspace(
    arg: str,
    *,
    client: SlashCommandClient,
    renderer: ChatRenderer,
) -> None:
    console = renderer.console
    if arg:
        try:
            summary = client.set_workspace(arg)
        except WorkspaceError as exc:
            console.print(Text(str(exc), style="err"))
            return
        console.print(
            Text(
                f"workspace: {summary.display_root} · session grants cleared",
                style="ok",
            )
        )
        return

    summary = client.workspace_summary()
    folders = (
        ", ".join(summary.granted_folders) if summary.granted_folders else "(none)"
    )
    console.print(Text(f"workspace: {summary.display_root}", style="fg"))
    console.print(Text(f"also allowed this session: {folders}", style="muted"))


def _render_tools(client: SlashCommandClient, renderer: ChatRenderer) -> None:
    console = renderer.console
    tools = client.tools_summary()
    console.print(Text("Built-in tools:", style="muted"))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="fg", no_wrap=True)
    grid.add_column(style="muted")
    for tool_info in tools:
        if tool_info.name == "set_persona":
            continue
        suffix = " (always asks y/n)" if tool_info.requires_approval else ""
        grid.add_row(f"  • {tool_info.name}", f"{tool_info.description}{suffix}")
    console.print(grid)


async def handle_slash_command(
    line: str,
    *,
    client: SlashCommandClient,
    renderer: ChatRenderer,
    prompt_line: Callable[[str], Awaitable[str]],
    on_reset_tokens: Callable[[], None] | None = None,
    input_dev: Input | None = None,
) -> bool:
    """Execute a `/command` line.

    Returns `True` when `/exit` is invoked and the chat loop should terminate.
    """
    stripped = line.strip()
    cmd_part, _, arg_part = stripped.partition(" ")
    cmd = cmd_part.lower()
    arg = arg_part.strip()

    if cmd == "/help":
        _render_help(renderer)
        return False

    if cmd == "/new":
        await client.new_conversation()
        if on_reset_tokens is not None:
            on_reset_tokens()
        renderer.clear_todo()
        renderer.render_new_conversation_divider()
        return False

    if cmd == "/history":
        try:
            event = await client.history(limit=HISTORY_FETCH_LIMIT)
        except ProtocolError as exc:
            renderer.console.print(Text(str(exc), style="warn"))
            return False
        if not event.messages:
            renderer.console.print(
                Text("No messages in this conversation yet.", style="muted")
            )
            return False
        renderer.render_history(
            event,
            updated_at=client.updated_at,
            show_messages=True,
        )
        return False

    if cmd == "/todo":
        try:
            from aldwin.cli.todo_view import TodoViewState

            todo_event = await client.todo_get()
            if todo_event.total > 0:
                renderer.todo_view.state = TodoViewState.from_event(todo_event)
        except ProtocolError as exc:
            renderer.console.print(Text(str(exc), style="warn"))
        renderer.todo_view.print_current(renderer.console)
        return False

    if cmd == "/model":
        await _handle_model_command(
            arg,
            client=client,
            renderer=renderer,
            prompt_line=prompt_line,
            input_dev=input_dev,
        )
        return False

    if cmd == "/persona":
        _render_persona(client, renderer)
        return False

    if cmd == "/memory":
        _render_memory(client, renderer)
        return False

    if cmd == "/skills":
        if arg.lower().startswith("show"):
            name = arg[4:].strip()
            if not name:
                renderer.console.print(Text("Usage: /skills show <name>", style="warn"))
                return False
            show_fn = getattr(client, "skills_show", None)
            if show_fn is None:
                renderer.console.print(
                    Text("Skill body lookup is unavailable.", style="warn")
                )
                return False
            try:
                body_event = await show_fn(name)
            except ProtocolError as exc:
                renderer.console.print(Text(str(exc), style="err"))
                return False
            body = getattr(body_event, "body", None)
            if body is None:
                renderer.console.print(Text(f"No skill named {name!r}.", style="err"))
                return False
            from aldwin.client import sanitize_display_text

            renderer.console.print(Text(sanitize_display_text(str(body)), style="fg"))
            return False
        list_fn = getattr(client, "skills_list", None)
        if list_fn is not None:
            try:
                event = await list_fn()
                from aldwin.client import SkillsReport, SkillSummary

                rows = tuple(
                    SkillSummary(
                        name=item.name,
                        description=item.description,
                        source=item.source,
                        flags=tuple(item.flags),
                        last_used=item.last_used,
                    )
                    for item in event.items
                )
                render_skills_report(SkillsReport(valid=rows), renderer.console)
                return False
            except ProtocolError as exc:
                renderer.console.print(Text(str(exc), style="warn"))
        _render_skills(client, renderer)
        return False

    if cmd == "/workspace":
        _render_workspace(arg, client=client, renderer=renderer)
        return False

    if cmd == "/verbose":
        renderer.set_verbose(not renderer.verbose)
        return False

    if cmd == "/tools":
        _render_tools(client, renderer)
        return False

    if cmd == "/exit":
        return True

    renderer.console.print(Text("Unknown command. Type /help.", style="warn"))
    return False
