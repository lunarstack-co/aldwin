"""Slash command completer and command handlers for Aldwin's chat screen."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Final, Protocol, cast

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.input import Input
from rich.table import Table
from rich.text import Text

from aldwin.cli.clipboard import copy_to_clipboard
from aldwin.cli.render import ChatRenderer, last_code_block
from aldwin.cli.theme import filter_canonical_models
from aldwin.client import (
    MemorySummary,
    PersonaSummary,
    ProtocolError,
    SearchSwitchResult,
    SkillsReport,
    ToolInfo,
    WorkspaceError,
    WorkspaceSummary,
    switchable_search_providers,
)
from aldwin.protocol import (
    DoneEvent,
    ErrorEvent,
    HistoryEvent,
    ModelsEvent,
    ProviderModels,
    TodoUpdatedEvent,
)

HISTORY_FETCH_LIMIT: Final[int] = 500

SLASH_COMMANDS: Final[tuple[tuple[str, str], ...]] = (
    ("/help", "Show commands and keyboard shortcuts"),
    ("/new", "Start a new conversation"),
    ("/history", "Show this conversation’s saved messages"),
    ("/copy", "Copy the last answer · /copy code for its last code block"),
    ("/model", "Switch model or provider"),
    ("/search", "Show or switch the web search provider"),
    ("/persona", "Show current persona and file path"),
    ("/memory", "Show profile, recent notes, and index status"),
    ("/skills", "List skills · /skills show <name> for the body"),
    ("/todo", "Show the current task checklist"),
    ("/workspace", "Show or switch the workspace folder"),
    ("/verbose", "Show or hide file previews for this session"),
    ("/notify", "Turn terminal notifications on or off for this session"),
    ("/tools", "List built-in tools (includes recall, remember, forget)"),
    ("/connectors", "Add and manage connected apps (MCP servers)"),
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

    async def list_models(
        self,
        *,
        provider: str | None = None,
        refresh: bool = False,
    ) -> ModelsEvent: ...

    async def set_model(self, name: str) -> DoneEvent | ErrorEvent: ...

    async def save_provider_key(
        self, provider: str, key: str
    ) -> DoneEvent | ErrorEvent: ...

    async def add_provider_server(
        self,
        name: str,
        base_url: str,
        *,
        key: str | None = None,
        title: str | None = None,
        local: bool = False,
    ) -> DoneEvent | ErrorEvent: ...

    def persona_summary(self) -> PersonaSummary: ...

    def memory_summary(self) -> MemorySummary: ...

    def skills_report(self) -> SkillsReport: ...

    def tools_summary(self) -> list[ToolInfo]: ...

    def workspace_summary(self) -> WorkspaceSummary: ...

    def set_workspace(self, new_root: Path | str) -> WorkspaceSummary: ...

    async def todo_get(self) -> TodoUpdatedEvent: ...

    def describe_active_search(self) -> str: ...

    def switch_search_provider(self, raw_name: str) -> SearchSwitchResult: ...

    def switchable_search_providers(self) -> tuple[str, ...]: ...

    def active_search_provider_name(self) -> str | None: ...


class SlashCompleter(Completer):
    """Prompt-toolkit completer for `/help`, `/new`, `/model`, etc.."""

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        _ = complete_event
        before = document.text_before_cursor.lstrip()
        if not before.startswith("/") or "\n" in before:
            return

        lowered = before.lower()
        # Once the user has typed `/search`, only complete provider names,
        # not `/search` again from the command list.
        if lowered == "/search" or lowered.startswith("/search "):
            parts = before.split(maxsplit=1)
            arg_prefix = parts[1].lower() if len(parts) == 2 else ""
            for name in switchable_search_providers():
                if name.startswith(arg_prefix):
                    yield Completion(
                        text=f"/search {name}",
                        start_position=-len(before),
                        display=name,
                        display_meta="search provider",
                    )
            return

        if " " in before:
            return

        prefix = lowered
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


COPY_NOTHING: Final[str] = "Nothing to copy yet."
COPY_NO_CODE: Final[str] = "The last answer has no code block."
COPY_FAILED: Final[str] = (
    "Couldn't reach the clipboard here. Select the text with the mouse instead."
)
COPY_USAGE: Final[str] = "Usage: /copy or /copy code"


def _handle_copy(arg: str, renderer: ChatRenderer) -> None:
    """Put the last answer, or its last code block, on the clipboard."""
    console = renderer.console
    mode = arg.lower()
    if mode not in ("", "code"):
        console.print(Text(COPY_USAGE, style="warn"))
        return

    text = renderer.last_answer
    if not text:
        console.print(Text(COPY_NOTHING, style="muted"))
        return
    if mode == "code":
        code = last_code_block(text)
        if code is None:
            console.print(Text(COPY_NO_CODE, style="muted"))
            return
        text = code

    method = copy_to_clipboard(
        text, stream=console.file, is_terminal=console.is_terminal
    )
    if method is None:
        console.print(Text(COPY_FAILED, style="warn"))
        return
    count = len(text.splitlines())
    unit = "line" if count == 1 else "lines"
    console.print(Text(f"✓ Copied {count} {unit}", style="muted"))


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
    refresh: bool = False,
) -> None:
    from aldwin.cli.picker import pick_index, row_text
    from aldwin.cli.secret_prompt import prompt_secret

    console = renderer.console
    show_all = arg.lower() == "all"
    titles = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "gemini": "Google Gemini",
        "openrouter": "OpenRouter",
        "deepseek": "DeepSeek",
        "ollama": "Ollama",
        "lmstudio": "LM Studio",
    }

    if arg and not show_all:
        res = await client.set_model(arg)
        if isinstance(res, ErrorEvent):
            renderer.render_error(res.code, res.message)
        else:
            cleaned = arg.strip()
            if "/" in cleaned:
                prov_part, _, mid = cleaned.partition("/")
            else:
                prov_part, mid = "openai", cleaned
            title = titles.get(prov_part.lower(), prov_part.title())
            local_suffix = (
                ", local" if prov_part.lower() in ("ollama", "lmstudio") else ""
            )
            console.print(
                Text(f"✓ Switched to {mid} ({title}{local_suffix}).", style="ok")
            )
        return

    use_picker = (
        isinstance(input_dev, Input) and console.is_interactive and not renderer.plain
    )

    async def _ask(prompt_text: str) -> str:
        res = prompt_line(prompt_text)
        if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
            return str(await res)
        return str(res)

    async def _ask_secret(prompt_text: str) -> str:
        res = prompt_secret(prompt_text)
        if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
            return str(await res)
        return str(res)

    models_ev = await client.list_models(refresh=refresh)
    if not isinstance(models_ev, ModelsEvent):
        models_ev = ModelsEvent(re="", current=client.model, providers=[])

    while True:
        curr = models_ev.current or client.model
        curr_clean = curr.strip()
        curr_provider = curr_clean.split("/")[0] if "/" in curr_clean else "openai"

        # Screen 1: Available providers
        # 1. Local providers (Ollama, LM Studio)
        # 2. Configured cloud companies (OpenAI, Anthropic, ...)
        # 3. Add a provider…
        # 4. Other server
        available_providers: list[ProviderModels] = [
            p for p in models_ev.providers if p.local or p.configured
        ]

        rows: list[str | Sequence[tuple[str, str]]] = []
        actions: list[tuple[str, Any]] = []
        disabled: set[int] = set()

        rows.append([("class:heading", "Available providers")])
        disabled.add(0)
        actions.append(("header", None))

        default_provider_idx = 0
        for p in available_providers:
            is_current = p.name == curr_provider
            current_tag = " (current)" if is_current else ""
            if is_current:
                default_provider_idx = len(rows)

            if p.status != "ok" and not p.models:
                row_label = f"{p.title}{current_tag}  (not running)"
            elif p.models:
                count_str = (
                    f"{len(p.models)} {'model' if len(p.models) == 1 else 'models'}"
                )
                row_label = f"{p.title}{current_tag}  ({count_str})"
            else:
                row_label = f"{p.title}{current_tag}"

            rows.append(row_label)
            actions.append(("provider", p))

        rows.append("Add a provider…")
        actions.append(("action_add", None))

        rows.append("Other server")
        actions.append(("action_server", None))

        chosen_idx: int | None = None
        if use_picker:
            chosen_idx = await pick_index(
                rows,
                default=default_provider_idx,
                disabled=disabled,
                input_dev=input_dev,
                plain=renderer.plain,
            )
        else:
            selectable_map: dict[int, int] = {}
            counter = 1
            for idx, r in enumerate(rows):
                if idx in disabled:
                    console.print(
                        Text(
                            f"  {row_text(r)}",
                            style="bold",
                        )
                    )
                else:
                    selectable_map[counter] = idx
                    console.print(Text(f"    {counter}. {row_text(r)}"))
                    counter += 1

            try:
                raw_choice = await _ask(
                    f"Choose provider [1-{counter - 1}, or Enter to cancel]: "
                )
            except (EOFError, KeyboardInterrupt):
                raw_choice = ""

            choice = raw_choice.strip()
            if not choice:
                console.print(Text(f"Model unchanged ({client.model}).", style="muted"))
                return
            if choice.isdigit() and int(choice) in selectable_map:
                chosen_idx = selectable_map[int(choice)]
            else:
                console.print(
                    Text(
                        f"Invalid selection {choice!r}. "
                        f"Enter a number from 1 to {len(selectable_map)}.",
                        style="err",
                    )
                )
                continue

        if chosen_idx is None:
            console.print(Text(f"Model unchanged ({client.model}).", style="muted"))
            return

        act_type, payload = actions[chosen_idx]
        if act_type == "provider":
            p = payload
            if not p.models:
                if p.status != "ok":
                    port_hint = (
                        ":11434"
                        if p.name == "ollama"
                        else (":1234" if p.name == "lmstudio" else "")
                    )
                    url_hint = f"http://localhost{port_hint}"
                    console.print(
                        Text(
                            f"{p.title} is not running at {url_hint}.",
                            style="warn",
                        )
                    )
                else:
                    console.print(
                        Text(
                            f"No models found for {p.title}.",
                            style="warn",
                        )
                    )
                continue

            # Screen 2: Models for selected provider
            models_to_display = p.models
            hidden_count = 0
            if p.name == "openai" and not show_all:
                all_ids = [m.id for m in p.models]
                curr_id = curr_clean.split("/")[-1]
                canonical_ids, hidden_count = filter_canonical_models(all_ids, curr_id)
                models_to_display = [m for m in p.models if m.id in canonical_ids]

            model_rows: list[str | Sequence[tuple[str, str]]] = []
            default_model_idx = 0
            for m in models_to_display:
                is_current = (
                    m.ref == curr_clean
                    or curr_clean == m.id
                    or ("/" not in curr_clean and m.ref == f"openai/{curr_clean}")
                )
                marker = " (current)" if is_current else ""
                tools_tick = (
                    "✓ tools"
                    if m.supports_tools is True
                    else ("✗ chat only" if m.supports_tools is False else "")
                )
                ctx = f"{m.context_window // 1000}K" if m.context_window else ""
                meta = " · ".join(x for x in [tools_tick, ctx] if x)
                meta_str = f"  ({meta})" if meta else ""
                if is_current:
                    default_model_idx = len(model_rows)
                model_rows.append(f"{m.id}{marker}{meta_str}")

            header_str = f"{p.title} models"
            if hidden_count > 0:
                header_str += (
                    f" (showing {len(models_to_display)} canonical · "
                    f"use /model all for all {len(p.models)})"
                )

            model_choice_idx: int | None = None
            if use_picker:
                model_choice_idx = await pick_index(
                    model_rows,
                    default=default_model_idx,
                    header=header_str,
                    input_dev=input_dev,
                    plain=renderer.plain,
                )
            else:
                console.print(Text(f"  {header_str}", style="bold"))
                for m_i, r in enumerate(model_rows, 1):
                    console.print(Text(f"    {m_i}. {r}"))
                try:
                    raw_m_choice = await _ask(
                        f"Choose model [1-{len(model_rows)}, or Enter to go back]: "
                    )
                except (EOFError, KeyboardInterrupt):
                    raw_m_choice = ""

                m_choice = raw_m_choice.strip()
                if not m_choice:
                    continue  # Go back to Screen 1
                if m_choice.isdigit() and 1 <= int(m_choice) <= len(models_to_display):
                    model_choice_idx = int(m_choice) - 1
                else:
                    console.print(
                        Text(
                            f"Invalid selection {m_choice!r}. "
                            f"Enter a number from 1 to {len(model_rows)}.",
                            style="err",
                        )
                    )
                    continue

            # Esc in model picker goes back to Screen 1
            if model_choice_idx is None:
                continue

            chosen_model = models_to_display[model_choice_idx]
            if chosen_model.supports_tools is False:
                console.print(
                    Text(
                        f"Warning: {chosen_model.id} does not support tools. "
                        "Aldwin will run in chat-only mode.",
                        style="warn",
                    )
                )
                try:
                    confirm = await _ask("Switch to chat-only model? [y/N]: ")
                except (EOFError, KeyboardInterrupt):
                    confirm = ""
                if confirm.strip().lower() not in ("y", "yes"):
                    console.print(
                        Text(f"Model unchanged ({client.model}).", style="muted")
                    )
                    return

            res = await client.set_model(chosen_model.ref)
            if isinstance(res, ErrorEvent):
                renderer.render_error(res.code, res.message)
            else:
                local_suffix = ", local" if p.local else ""
                console.print(
                    Text(
                        f"✓ Switched to {chosen_model.id} ({p.title}{local_suffix}).",
                        style="ok",
                    )
                )
            return

        elif act_type == "action_add":
            unconfigured = [
                p for p in models_ev.providers if not p.local and not p.configured
            ]
            if not unconfigured:
                console.print(
                    Text(
                        "All supported cloud providers are already configured.",
                        style="muted",
                    )
                )
                continue
            prov_labels = [p.title for p in unconfigured]
            p_choice = (
                await pick_index(
                    prov_labels,
                    header="Add a provider",
                    input_dev=input_dev,
                    plain=renderer.plain,
                )
                if use_picker
                else None
            )
            if p_choice is None and not use_picker:
                for i, pl in enumerate(prov_labels, 1):
                    console.print(Text(f"  {i}. {pl}"))
                try:
                    c_raw = (
                        await _ask(
                            f"Choose provider [1-{len(prov_labels)}, "
                            "Enter to go back]: "
                        )
                    ).strip()
                    if c_raw.isdigit() and 1 <= int(c_raw) <= len(prov_labels):
                        p_choice = int(c_raw) - 1
                except (EOFError, KeyboardInterrupt):
                    pass

            if p_choice is None:
                continue  # Go back to Screen 1

            chosen_p = unconfigured[p_choice]
            try:
                key_val = (await _ask_secret(f"{chosen_p.title} API key: ")).strip()
            except (EOFError, KeyboardInterrupt):
                key_val = ""
            if not key_val:
                console.print(Text("Cancelled.", style="muted"))
                continue
            res = await client.save_provider_key(chosen_p.name, key_val)
            if isinstance(res, ErrorEvent):
                renderer.render_error(res.code, res.message)
            else:
                console.print(Text(f"✓ Saved key for {chosen_p.title}.", style="ok"))
                models_ev = await client.list_models(refresh=True)

        elif act_type == "action_server":
            try:
                address = (
                    await _ask("Server address (e.g. http://localhost:8000/v1): ")
                ).strip()
                if not address:
                    continue
                key_val = (await _ask_secret("API key (Enter to skip): ")).strip()
                name = (await _ask("Provider name (e.g. office-gpu): ")).strip()
                if not name:
                    continue
            except (EOFError, KeyboardInterrupt):
                continue
            res = await client.add_provider_server(
                name=name,
                base_url=address,
                key=key_val or None,
                title=name,
            )
            if isinstance(res, ErrorEvent):
                renderer.render_error(res.code, res.message)
            else:
                console.print(Text(f"✓ Added provider {name}.", style="ok"))
                models_ev = await client.list_models(refresh=True)


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
        suffix = f" ({tool_info.approval_note})" if tool_info.approval_note else ""
        grid.add_row(f"  • {tool_info.name}", f"{tool_info.description}{suffix}")
    console.print(grid)


async def _render_connector_tools(client: object, renderer: ChatRenderer) -> None:
    """The `/tools` Connectors section: each connector's tools and their setting."""
    list_fn = getattr(client, "connectors_list", None)
    if list_fn is None:
        return
    try:
        listing = await list_fn()
    except ProtocolError:
        return
    if not listing.items:
        return
    from aldwin.client import sanitize_display_text

    console = renderer.console
    console.print(Text("Connectors:", style="muted"))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="fg", no_wrap=True)
    grid.add_column(style="muted")
    for info in listing.items:
        grid.add_row(f"  {sanitize_display_text(info.title)}", info.status)
        for tool in info.tools:
            kind = "read" if tool.read_only else "write"
            name = sanitize_display_text(tool.name)
            grid.add_row(f"    • {name}", f"{kind} · {tool.effective}")
    console.print(grid)


async def _handle_search_command(
    arg: str,
    *,
    client: SlashCommandClient,
    renderer: ChatRenderer,
) -> None:
    """Show or switch the persisted web search provider (`/search`)."""
    console = renderer.console
    if not arg:
        status = await asyncio.to_thread(client.describe_active_search)
        console.print(Text(status, style="muted"))
        return

    result = await asyncio.to_thread(client.switch_search_provider, arg)
    if result.ok:
        status = await asyncio.to_thread(client.describe_active_search)
        console.print(Text(status, style="muted"))
        return
    console.print(Text(result.message, style="warn"))
    if "Unknown search provider" in result.message:
        names = ", ".join(client.switchable_search_providers())
        console.print(Text(f"Usage: /search [{names}]", style="muted"))


async def handle_slash_command(
    line: str,
    *,
    client: SlashCommandClient,
    renderer: ChatRenderer,
    prompt_line: Callable[[str], Awaitable[str]],
    on_reset_tokens: Callable[[], None] | None = None,
    input_dev: Input | None = None,
    prompt_secret: Callable[[str], Awaitable[str]] | None = None,
    home: Path | None = None,
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
            model=client.model,
        )
        return False

    if cmd == "/copy":
        _handle_copy(arg, renderer)
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

    if cmd == "/search":
        await _handle_search_command(arg, client=client, renderer=renderer)
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

    if cmd == "/notify":
        renderer.set_notifications(not renderer.notifications_enabled)
        return False

    if cmd == "/tools":
        _render_tools(client, renderer)
        await _render_connector_tools(client, renderer)
        return False

    if cmd == "/connectors":
        if getattr(client, "connectors_list", None) is None:
            renderer.console.print(Text("Connectors are unavailable.", style="warn"))
            return False
        from aldwin.cli.connectors_view import (
            ConnectorsClient,
            handle_connectors_command,
        )
        from aldwin.cli.secret_prompt import prompt_secret as default_secret

        await handle_connectors_command(
            client=cast(ConnectorsClient, client),
            renderer=renderer,
            prompt_line=prompt_line,
            prompt_secret=prompt_secret or default_secret,
            input_dev=input_dev,
        )
        return False

    if cmd == "/exit":
        return True

    renderer.console.print(Text("Unknown command. Type /help.", style="warn"))
    return False
