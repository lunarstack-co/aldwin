"""Rich inline renderer for Aldwin's welcome, history, streaming markdown, and tools."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Final, Literal

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from aldwin.cli.theme import (
    ACCENT_BRIGHT_RGB,
    ACCENT_RGB,
    GLYPH_FRAME_S,
    SPINNER_FRAMES,
    STALL_RED_RGB,
    format_duration_ms,
    format_elapsed_s,
    format_relative_time,
    format_tokens,
    interpolate_rgb,
    reduced_motion_preferred,
    rgb_style,
    sync_output_enabled,
)
from aldwin.cli.todo_view import TodoView
from aldwin.client import sanitize_display_text
from aldwin.protocol import (
    ApprovalAnswer,
    ApprovalEvent,
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    HistoryEvent,
    TodoUpdatedEvent,
    ToolCallEvent,
    ToolResultEvent,
)

MAX_PANEL_WIDTH: Final[int] = 76
MAX_DIFF_PREVIEW_LINES: Final[int] = 24

ERROR_COPY: Final[dict[ErrorCode, str]] = {
    "auth_failed": "Your OpenAI key was rejected. Run `aldwin setup`.",
    "rate_limited": (
        "OpenAI is rate-limiting requests right now. Wait a moment and try again."
    ),
    "model_unavailable": (
        "Can't reach OpenAI. Check your connection, then press ↑ Enter to retry."
    ),
    "unknown_model": (
        "That model isn't available to your key. Pick another with /model."
    ),
    "busy": (
        "Aldwin is still working on your last request. Press Esc to stop it first."
    ),
    "version_mismatch": (
        "Protocol version mismatch with host. Restart Aldwin and try again."
    ),
    "protocol": "Something went wrong. Details are in ~/.aldwin/logs/aldwin.log",
    "unknown_approval": (
        "That approval prompt has already expired. Try your request again."
    ),
    "internal": "Something went wrong. Details are in ~/.aldwin/logs/aldwin.log",
    "not_found": "Something went wrong. Details are in ~/.aldwin/logs/aldwin.log",
}

INTERRUPTED_NOTICE: Final[str] = (
    "Your last request was interrupted. Say *continue* to pick it up."
)
MAX_STEPS_NOTICE: Final[str] = "Stopped {steps}. Say *continue* to keep going."


def _steps_phrase(max_steps: int | None) -> str:
    """Describe the step limit for the max-steps notice."""
    if max_steps is None:
        return "at the step limit"
    unit = "step" if max_steps == 1 else "steps"
    return f"after {max_steps} {unit}"


CONTEXT_NEARLY_FULL_NOTICE: Final[str] = (
    "Context nearly full. Older messages will be dropped; /new starts fresh."
)
# Fallback tip when rotation is unavailable (tests / first-run body).
TIPS_LINE: Final[str] = (
    'Try: "what can you do?"  ·  /help  ·  /history  ·  Esc stops an answer'
)

STALL_START_S: Final[float] = 8.0
STALL_FULL_S: Final[float] = 12.0
CSI_SYNC_START: Final[str] = "\x1b[?2026h"
CSI_SYNC_END: Final[str] = "\x1b[?2026l"

_HUMAN_TOOL_LABELS: Final[dict[str, str]] = {
    "list_dir": "list",
    "glob": "search",
    "grep": "search",
    "read_file": "Read",
    "edit_file": "Edit",
    "write_file": "Write",
    "read_document": "Read",
    "download_file": "Download",
    "run_command": "shell",
    "web_search": "Search",
    "web_fetch": "Fetch",
    "remember": "Remember",
    "recall": "Recall",
    "forget": "Forget",
    "todo": "tasks",
    "calculate": "Calculate",
    "create_skill": "skill",
    "set_persona": "persona",
    "load_skill": "skill",
}


def connector_row_label(event: ToolCallEvent) -> str:
    """`⚙ <Connector> · <tool>` for a connector tool row."""
    title = sanitize_display_text(event.connector_title or event.connector or "")
    tool = sanitize_display_text(event.remote_tool or event.name)
    return f"⚙ {title} · {tool}"


def human_tool_label(tool_name: str) -> str:
    """Return a short human label for a tool identifier (never the raw id alone)."""
    return _HUMAN_TOOL_LABELS.get(tool_name, tool_name.replace("_", " "))


_TRUNCATED_JSON_STR_RE: Final[re.Pattern[str]] = re.compile(
    r'^\{\s*"(?P<key>command|url|name|query|text|answer)"\s*:\s*"(?P<val>.*)$'
)


def _cap_text_width(text: str, limit: int) -> str:
    """Truncate `text` to at most `limit` characters with a trailing `…`."""
    if limit <= 1:
        return text[:limit]
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def format_display_args(args_preview: str, tool_name: str = "") -> str:
    """Convert a JSON `args_preview` into a concise human-readable argument string."""
    cleaned = args_preview.strip()
    if not cleaned:
        return ""

    if cleaned.startswith("persona.md"):
        return cleaned

    if tool_name == "set_persona" or '"new_text"' in cleaned:
        try:
            parsed_persona = json.loads(cleaned)
            if isinstance(parsed_persona, dict) and isinstance(
                parsed_persona.get("new_text"), str
            ):
                raw_text = parsed_persona["new_text"].strip()
                count = len(raw_text.splitlines()) if raw_text else 0
                unit = "line" if count == 1 else "lines"
                return f"persona.md · {count} {unit}"
        except json.JSONDecodeError:
            count = max(1, cleaned.count("\\n") + 1)
            unit = "line" if count == 1 else "lines"
            return f"persona.md · {count} {unit}"

    if not (cleaned.startswith("{") and cleaned.endswith("}")):
        if cleaned.startswith("{"):
            match = _TRUNCATED_JSON_STR_RE.match(cleaned)
            if match is not None:
                key = match.group("key")
                val = match.group("val")
                if val.endswith('"'):
                    val = val[:-1]
                if key in ("query", "text"):
                    return f'"{val}"'
                return val
        return cleaned

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return cleaned

    if not isinstance(parsed, dict) or not parsed:
        return cleaned

    for raw_key in ("command", "url", "name"):
        if raw_key in parsed and isinstance(parsed[raw_key], str):
            return parsed[raw_key]

    for quoted_key in ("query", "text"):
        if quoted_key in parsed and isinstance(parsed[quoted_key], str):
            val = parsed[quoted_key]
            if len(parsed) == 1:
                return f'"{val}"'
            extras = [f"{k}={v}" for k, v in parsed.items() if k != quoted_key]
            return f'"{val}" ({", ".join(extras)})'

    if len(parsed) == 1:
        only_val = next(iter(parsed.values()))
        if isinstance(only_val, str):
            return f'"{only_val}"'
        return str(only_val)

    parts = [
        f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}" for k, v in parsed.items()
    ]
    return ", ".join(parts)


def clean_tool_summary(tool_name: str, summary: str) -> str:
    """Normalise internal tool summaries (such as `# Using Aldwin` on `load_skill`)."""
    cleaned = summary.strip()
    if tool_name == "load_skill" and cleaned.startswith("#"):
        return "loaded"
    return cleaned


def _panel_width(console: Console) -> int:
    return min(console.width, MAX_PANEL_WIDTH)


def _opening_fence(stripped_line: str) -> str | None:
    """Return the fence marker (` ``` ` or `~~~`) if `stripped_line` opens a fence."""
    for char in ("`", "~"):
        if stripped_line.startswith(char * 3):
            run_len = len(stripped_line) - len(stripped_line.lstrip(char))
            return char * run_len
    return None


def _is_closing_fence(stripped_line: str, fence: str) -> bool:
    """Return `True` if `stripped_line` closes an open code `fence`."""
    fence_char = fence[0]
    if not stripped_line.startswith(fence):
        return False
    rest = stripped_line.lstrip(fence_char).strip()
    return rest == ""


def split_markdown_blocks(buffer: str) -> tuple[list[str], str]:
    """Split `buffer` on blank lines outside code fences into `(completed, remainder)`.

    Blank lines inside ` ``` ` or `~~~` fenced code blocks never split the block.
    """
    if not buffer:
        return [], ""

    completed: list[str] = []
    current_lines: list[str] = []
    open_fence: str | None = None

    for line in buffer.splitlines(keepends=True):
        stripped = line.strip()
        if open_fence is None:
            fence = _opening_fence(stripped)
            if fence is not None:
                open_fence = fence
                current_lines.append(line)
                continue

            if stripped == "" and line.endswith(("\n", "\r")):
                block = "".join(current_lines).strip()
                if block:
                    completed.append(block)
                current_lines = []
                continue

            current_lines.append(line)
        else:
            current_lines.append(line)
            if _is_closing_fence(stripped, open_fence):
                open_fence = None

    remainder = "".join(current_lines)
    return completed, remainder


def _tool_word(count: int) -> str:
    return "1 tool" if count == 1 else f"{count} tools"


def _approval_record_target(event: ApprovalEvent) -> str:
    summary = event.summary.strip()
    # Diff summaries already name the file (+/−); don't treat the hunk as persona.
    if event.tier == "diff":
        return summary
    detail = event.detail.strip()
    if not detail:
        return summary
    if "\n" in detail or "persona" in summary.lower():
        line_count = len(detail.splitlines())
        unit = "line" if line_count == 1 else "lines"
        return f"persona.md ({line_count} {unit})"
    return detail


def _diff_line_style(line: str, *, plain: bool) -> str:
    """Return the theme style for one unified-diff line."""
    if plain:
        return ""
    if line.startswith("+") and not line.startswith("+++"):
        return "diff.plus"
    if line.startswith("-") and not line.startswith("---"):
        return "diff.minus"
    if line.startswith(("@@", "---", "+++")):
        return "diff.meta"
    return "diff.context"


class ChatRenderer:
    """Renders session headers, history, approvals, and errors to a Rich `Console`."""

    def __init__(self, console: Console, *, plain: bool = False) -> None:
        self.console = console
        self.plain = plain
        self.verbose = False
        self.todo_view = TodoView(plain=plain, unicode=not plain)

    def set_verbose(self, enabled: bool) -> None:
        """Turn file-tool previews on or off for this session and say which."""
        self.verbose = enabled
        state = "on" if enabled else "off"
        self.console.print(f"verbose: {state}")

    def on_todo_updated(self, event: TodoUpdatedEvent) -> None:
        """Redraw the session checklist from a `todo.updated` event."""
        self.todo_view.apply_event(event, self.console)

    def clear_todo(self) -> None:
        """Clear checklist state after `/new`."""
        self.todo_view.clear()

    def render_welcome(
        self,
        *,
        first_run: bool,
        model: str,
        display_home: str,
        display_workspace: str | None = None,
        tip: str | None = None,
    ) -> None:
        """Render the everyday welcome card (borderless, no panel chrome)."""
        from aldwin.cli.brand import TAGLINE

        width = _panel_width(self.console)
        if first_run:
            title = Text("Welcome to Aldwin", style="accent")
            body = Text(TAGLINE, style="fg")
            self.console.print(title)
            self.console.print(body)
            return

        raw_loc = (
            f"workspace: {display_workspace}" if display_workspace else display_home
        )
        max_loc = max(12, width - len(model) - 18)
        shown_loc = (
            raw_loc if len(raw_loc) <= max_loc else f"…{raw_loc[-(max_loc - 1) :]}"
        )
        title = Text()
        title.append("Aldwin", style="accent")
        title.append(f" · {model} · {shown_loc}", style="muted")
        self.console.print(title)
        if tip:
            self.console.print(Text(tip, style="muted"))

    def render_update_nudge(self, line: str) -> None:
        """Print one muted PyPI update-available line under the welcome card."""
        self.console.print(Text(sanitize_display_text(line), style="muted"))

    def render_whats_new(self, text: str, *, version: str) -> None:
        """Print the one-time upgrade What's new panel under the welcome card."""
        panel_width = _panel_width(self.console)
        self.console.print(
            Panel(
                Text(sanitize_display_text(text), style="fg"),
                title=f"What's new in {version}",
                title_align="left",
                border_style="" if self.plain else "border",
                width=panel_width,
                padding=(0, 1),
            )
        )

    def render_history(
        self,
        event: HistoryEvent,
        *,
        updated_at: str | None = None,
        now: datetime | None = None,
        show_messages: bool = False,
        max_messages: int | None = None,
    ) -> None:
        """Render resume chrome and optionally the transcript.

        On open, call with ``show_messages=False`` (welcome + muted
        ``continuing from …`` only). ``/history`` uses ``show_messages=True``.
        """
        last_at = updated_at
        for item in event.messages:
            if item.at:
                last_at = item.at

        if show_messages and event.messages:
            shown = (
                event.messages
                if max_messages is None
                else event.messages[-max_messages:]
            )
            for item in shown:
                text = item.text
                if item.role == "user":
                    marker = "> " if self.plain else "❯ "
                    self.console.print(Text(f"{marker}{text}", style="muted"))
                else:
                    marker = "* " if self.plain else "● "
                    self.console.print(Text(f"{marker}{text}", style="muted"))
            self.console.print()

        if event.messages:
            when = format_relative_time(last_at, now=now)
            if show_messages:
                self.console.print(
                    Text(f"{len(event.messages)} messages · from {when}", style="muted")
                )
            else:
                self.console.print(
                    Text(
                        f"continuing from {when} · /history to review",
                        style="muted",
                    )
                )

        if event.interrupted:
            if self.plain:
                self.console.print(INTERRUPTED_NOTICE)
            else:
                notice = Text("Your last request was interrupted. Say ", style="warn")
                notice.append("continue", style="italic")
                notice.append(" to pick it up.")
                self.console.print(notice)

    def render_new_conversation_divider(self) -> None:
        """Render the divider shown when `/new` starts a fresh conversation."""
        if self.plain:
            self.console.print("──── new conversation ────")
        else:
            self.console.print(Rule("new conversation", style="muted", characters="─"))

    def render_turn_separator(self) -> None:
        """Horizontal rule after a finished Q&A so the next prompt is easy to spot."""
        self.console.print()
        if self.plain:
            width = max(12, min(48, self.console.width))
            self.console.print("─" * width)
        else:
            self.console.print(Rule(style="border", characters="─"))

    def build_approval_panel(
        self,
        event: ApprovalEvent,
        *,
        expanded: bool = False,
        selected: int = 0,
    ) -> Panel:
        """Construct the approval `Panel` (muted border; yellow only for risk)."""
        clean_summary = sanitize_display_text(event.summary.strip())
        clean_detail = sanitize_display_text(event.detail.strip())
        detail_text = clean_detail or clean_summary
        panel_width = _panel_width(self.console)
        # Panel padding=(0,1) + borders: keep folded diff lines scannable.
        inner_width = max(24, panel_width - 4)

        risky_labels = [
            sanitize_display_text(lbl) for lbl in event.risky if lbl and lbl.strip()
        ]

        if event.tier == "diff":
            lines = detail_text.splitlines() or [detail_text]
            if not expanded and len(lines) > MAX_DIFF_PREVIEW_LINES:
                shown_lines = lines[:MAX_DIFF_PREVIEW_LINES]
                more_count = len(lines) - MAX_DIFF_PREVIEW_LINES
            else:
                shown_lines = lines
                more_count = 0

            diff_parts: list[RenderableType] = []
            if risky_labels:
                diff_parts.append(Text(f"⚠ {' · '.join(risky_labels)}", style="warn"))
            truncated_long = 0
            for line in shown_lines:
                style = _diff_line_style(line, plain=self.plain)
                # Folded: clip long lines so the panel stays scannable.
                # Expanded: hard-wrap ourselves. Rich Panel soft-wrap can
                # collapse a tall Group to one ellipsized row in some terminals.
                if not expanded and len(line) > inner_width:
                    truncated_long += 1
                    diff_parts.append(
                        Text(
                            _cap_text_width(line, inner_width),
                            style=style,
                            overflow="ellipsis",
                            no_wrap=True,
                        )
                    )
                elif expanded and len(line) > inner_width:
                    for start in range(0, len(line), inner_width):
                        diff_parts.append(
                            Text(
                                line[start : start + inner_width],
                                style=style,
                                overflow="ellipsis",
                                no_wrap=True,
                            )
                        )
                else:
                    diff_parts.append(
                        Text(
                            line,
                            style=style,
                            overflow="ellipsis",
                            no_wrap=True,
                        )
                    )

            if more_count > 0:
                diff_parts.append(
                    Text(
                        f"… {more_count} more lines (ctrl+o to expand)",
                        style="muted",
                    )
                )
            elif truncated_long > 0:
                diff_parts.append(
                    Text(
                        "… long lines truncated (ctrl+o to expand)",
                        style="muted",
                    )
                )

            body_parts = list(diff_parts)
            body_parts.append(Text(""))
            body_parts.append(self._approval_chooser_options(event, selected=selected))
            body_renderable = Group(*body_parts)
        else:
            parts: list[RenderableType] = []
            if risky_labels:
                parts.append(Text(f"⚠ {' · '.join(risky_labels)}", style="warn"))
            detail_style = (
                "" if self.plain else ("fg" if "\n" in detail_text else "code")
            )
            parts.append(Text(detail_text, style=detail_style))
            parts.append(Text(""))
            parts.append(self._approval_chooser_options(event, selected=selected))
            body_renderable = Group(*parts)

        # Yellow border only when something is labelled risky; Diff is quiet grey.
        border = "warn" if risky_labels else "border"
        title = clean_summary or "Approve action?"
        return Panel(
            body_renderable,
            title=title,
            title_align="left",
            subtitle="Do you want to proceed?",
            subtitle_align="left",
            border_style="" if self.plain else border,
            width=panel_width,
            padding=(0, 1),
        )

    def _approval_chooser_options(
        self,
        event: ApprovalEvent,
        *,
        selected: int = 0,
    ) -> Text:
        """❯ list with No as default. ``selected`` is 0=No, 1=Yes, 2=session."""
        options: list[tuple[ApprovalAnswer, str]] = [
            ("no", "No"),
            ("yes", "Yes"),
        ]
        if event.grantable:
            if event.grant_scope == "folder":
                options.append(("yes_session", "Yes, for this folder this session"))
            elif event.grant_scope == "tool":
                options.append(("yes_session", "Yes for this session"))
            else:
                options.append(("yes_session", "Yes, for this session"))

        n = len(options)
        sel = selected % n if n else 0
        block = Text()
        for idx, (_answer, label) in enumerate(options):
            marker = "❯ " if idx == sel else "  "
            style = "accent" if idx == sel else "muted"
            if self.plain:
                marker = "> " if idx == sel else "  "
                style = ""
            block.append(f"{marker}{label}\n", style=style)
        block.append(
            "↑↓ move · enter confirm · y/n/a shortcuts",
            style="muted",
        )
        return block

    def ring_bell(self) -> None:
        """Emit the terminal bell character (`\\a`) when not in plain mode."""
        if not self.plain:
            try:
                self.console.file.write("\a")
                self.console.file.flush()
            except OSError:
                pass

    def render_approval_panel(
        self,
        event: ApprovalEvent,
        *,
        expanded: bool = False,
    ) -> None:
        """Print the bordered approval panel and ring the terminal bell."""
        self.ring_bell()
        self.console.print(
            self.build_approval_panel(event, expanded=expanded, selected=0)
        )

    def render_error(self, code: ErrorCode, message: str = "") -> None:
        """Render a protocol `ErrorEvent` as one actionable line."""
        # Host sends the full unknown_model sentence (includes the model name).
        # Prefer host "busy" copy for rate_limited so 503 is not shown as rate-limit.
        stripped = message.strip()
        lowered = stripped.lower()
        if code == "unknown_model" and stripped and "isn't available" in lowered:
            copy = stripped
        elif code == "rate_limited" and stripped and "busy" in lowered:
            copy = stripped
        else:
            copy = ERROR_COPY.get(code) or message or ERROR_COPY["internal"]
        line = Text()
        line.append("✗ ", style="err")
        line.append(copy, style="err")
        self.console.print(line)

    def render_status_notice(self, message: str) -> None:
        """Render a non-terminal status notice such as a retry message."""
        self.console.print(Text(f"  {message}", style="muted"))

    def render_context_notice(self) -> None:
        """Print the one-shot nearly-full context warning."""
        self.console.print(Text(CONTEXT_NEARLY_FULL_NOTICE, style="muted"))

    def start_turn(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> TurnRenderer:
        """Create and start a `TurnRenderer` for a new conversation turn."""
        self.todo_view.reset_turn()
        turn = TurnRenderer(self, clock=clock)
        turn.start()
        return turn


class TurnRenderer:
    """Streams markdown blocks, tool cards, and a live status line for one turn."""

    def __init__(
        self,
        chat_renderer: ChatRenderer,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._chat = chat_renderer
        self.console = chat_renderer.console
        self.plain = chat_renderer.plain
        self._clock = clock

        self._started_at: float = self._clock()
        self._last_activity_at: float = self._started_at
        self._spinner_mode: Literal[
            "requesting", "responding", "tool_use", "waiting"
        ] = "requesting"
        self._active_tool_name: str = ""
        self._token_estimate: int = 0
        self._tool_count: int = 0
        self._pending_tools: dict[str, tuple[str, str, str]] = {}
        # Connector rows: call id -> (`⚙ <Title> · <tool>`, policy reason).
        self._connector_rows: dict[str, tuple[str, str | None]] = {}
        self._printed_pending_ids: set[str] = set()
        self._markdown_buffer: str = ""
        self._has_printed_text: bool = False
        self._last_printed_kind: Literal["none", "tool", "text"] = "none"
        self._retry_status: str | None = None
        self._live: Live | None = None
        self._approval_live: Live | None = None
        self._approval_event: ApprovalEvent | None = None
        self._approval_selected: int = 0
        self._approval_expanded: bool = False
        self._reduced_motion: bool = reduced_motion_preferred()
        self._sync_output: bool = sync_output_enabled(plain=self.plain)

    @property
    def tool_count(self) -> int:
        """Return the number of completed tool calls in this turn."""
        return self._tool_count

    def elapsed_seconds(self) -> float:
        """Return elapsed seconds since the turn started."""
        return max(0.0, self._clock() - self._started_at)

    def _note_activity(self) -> None:
        """Reset stall timer after text / tool / status activity."""
        self._last_activity_at = self._clock()

    def _clear_retry_status(self) -> None:
        """Drop the in-place retry line once real turn activity resumes."""
        self._retry_status = None

    @property
    def _spinner_text(self) -> str:
        """Status verb derived from the current spinner mode."""
        if self._retry_status:
            return self._retry_status
        if self._spinner_mode == "responding":
            return "Writing…"
        if self._spinner_mode == "waiting":
            return "Waiting for you…"
        if self._spinner_mode == "tool_use":
            label = human_tool_label(self._active_tool_name or "tool")
            return f"Running {label}…"
        return "Thinking…"

    def format_status_line(self) -> str:
        """Muted trailing status after 3 s: `(Ns · ↓ N tokens · esc to interrupt)`."""
        elapsed = self.elapsed_seconds()
        if elapsed < 3.0:
            return ""
        tokens = format_tokens(self._token_estimate) if self._token_estimate else "…"
        return f"({format_elapsed_s(elapsed)} · ↓ {tokens} tokens · esc to interrupt)"

    def _stall_rgb(self) -> tuple[int, int, int]:
        silence = max(0.0, self._clock() - self._last_activity_at)
        if silence < STALL_START_S:
            return ACCENT_RGB
        if silence >= STALL_FULL_S:
            return STALL_RED_RGB
        t = (silence - STALL_START_S) / (STALL_FULL_S - STALL_START_S)
        return interpolate_rgb(ACCENT_RGB, STALL_RED_RGB, t)

    def spinner_glyph(self, *, now: float | None = None) -> str:
        """Return the current spinner glyph for ``now`` (injectable for tests)."""
        if self.plain:
            return "[.]"
        if self._reduced_motion:
            return "●"
        at = now if now is not None else self._clock()
        elapsed = max(0.0, at - self._started_at)
        frame_i = int(elapsed / GLYPH_FRAME_S) % len(SPINNER_FRAMES)
        return SPINNER_FRAMES[frame_i]

    def _build_spinner_line(self) -> Text:
        """Glyph + shimmer verb + optional elapsed trailer for the Live footer."""
        elapsed = self.elapsed_seconds()
        stall = self._stall_rgb()
        stall_style = rgb_style(*stall)
        shimmer_on = int(elapsed / 0.25) % 2 == 0
        verb_style = (
            stall_style
            if stall != ACCENT_RGB
            else (
                rgb_style(*ACCENT_BRIGHT_RGB) if shimmer_on else rgb_style(*ACCENT_RGB)
            )
        )

        if self._reduced_motion:
            glyph = "●"
            breathe = int(elapsed / 2.0) % 2 == 0
            glyph_style = verb_style if breathe else "muted"
        else:
            glyph = self.spinner_glyph()
            glyph_style = stall_style

        line = Text()
        line.append(f"{glyph} ", style=glyph_style)
        line.append(self._spinner_text, style=verb_style)
        trailer = self.format_status_line()
        if trailer:
            line.append(f"  {trailer}", style="muted")
        return line

    def _build_pending_tool_line(
        self,
        name: str,
        display_args: str,
        summary: str = "",
    ) -> Text:
        if summary:
            card = Text("  ● ", style="muted", no_wrap=True, overflow="ellipsis")
            card.append(summary, style="muted")
            return card
        card = Text("  ● ", style="muted", no_wrap=True, overflow="ellipsis")
        label = human_tool_label(name)
        card.append(label, style="muted")
        if display_args:
            card.append(f"  {display_args}", style="muted")
        return card

    def _build_live_renderable(self) -> RenderableType:
        """Build the composite `Group` displayed inside the transient `Live` region."""
        parts: list[RenderableType] = []
        for call_id, (tool_name, display_args, summary) in self._pending_tools.items():
            if call_id not in self._printed_pending_ids:
                parts.append(
                    self._build_pending_tool_line(tool_name, display_args, summary)
                )

        if self._markdown_buffer.strip():
            parts.append(Markdown(self._markdown_buffer))

        parts.append(self._build_spinner_line())
        return Group(*parts)

    def start(self) -> None:
        """Start the transient `Live` display at ~12 Hz (auto_refresh)."""
        # Blank line between the user's question and Aldwin's reply / spinner.
        self.console.print()
        self._started_at = self._clock()
        self._last_activity_at = self._started_at
        self._spinner_mode = "requesting"
        self._active_tool_name = ""
        self._ensure_live()

    def _stop_approval_live(self) -> None:
        if self._approval_live is not None:
            self._approval_live.stop()
            self._approval_live = None

    def _end_sync_output(self) -> None:
        if not self._sync_output:
            return
        try:
            self.console.file.write(CSI_SYNC_END)
            self.console.file.flush()
        except OSError:
            pass

    def _stop_live(self) -> None:
        self._stop_approval_live()
        if self._live is not None:
            try:
                self._live.stop()
            finally:
                self._end_sync_output()
            self._live = None

    def close(self) -> None:
        """Ensure turn and checklist Live regions are stopped; clear DEC 2026."""
        self._stop_live()
        self._chat.todo_view.freeze()

    def pause_for_todo(self) -> None:
        """Stop the turn spinner so the checklist can own the display region."""
        self._stop_live()

    def _ensure_live(self) -> None:
        """Start (or keep) the turn Live, never nesting with the checklist Live."""
        if self.plain:
            return
        if self._chat.todo_view.live_active:
            return
        if self._live is None:
            self._live = Live(
                get_renderable=self._build_live_renderable,
                console=self.console,
                refresh_per_second=12,
                transient=True,
                auto_refresh=True,
            )
            self._live.start(refresh=True)

    def _refresh_live(self) -> None:
        """Mark the Live region dirty; auto_refresh paints at 12 Hz.

        Explicit refresh is reserved for approval / resume edges. Text deltas
        only call ``_note_activity`` so streaming stays within the write budget.
        """
        if self._chat.todo_view.live_active:
            return
        self._ensure_live()
        if self._live is None or not self._live.is_started:
            return
        if not self._sync_output:
            return
        try:
            self.console.file.write(CSI_SYNC_START)
            self.console.file.flush()
        except OSError:
            pass
        try:
            self._live.refresh()
        finally:
            self._end_sync_output()

    def _print_markdown_block(self, block: str) -> None:
        cleaned = block.strip()
        if not cleaned:
            return
        if self._last_printed_kind in ("tool", "text"):
            self.console.print()
        lead = not self._has_printed_text
        if self.plain:
            for i, line in enumerate(cleaned.splitlines() or [""]):
                if lead and i == 0:
                    prefix = "* "
                else:
                    prefix = "  "
                self.console.print(f"{prefix}{line}")
        else:
            try:
                from aldwin.cli.markdown import render_markdown

                renderable = render_markdown(cleaned, plain=False)
            except ImportError:
                renderable = Markdown(cleaned)
            if lead:
                self.console.print(Text("● ", style="accent"), end="")
            self.console.print(renderable)
        self._has_printed_text = True
        self._last_printed_kind = "text"

    def _flush_markdown_buffer(self, *, suffix: str = "") -> None:
        raw = self._markdown_buffer.strip()
        self._markdown_buffer = ""
        if not raw:
            if suffix:
                self.console.print(Text(suffix, style="muted"))
            return
        combined = f"{raw} {suffix}".strip() if suffix else raw
        self._print_markdown_block(combined)

    def on_text(self, delta: str) -> None:
        """Append a `text` delta and flush finished markdown blocks."""
        if not delta:
            return
        self._clear_retry_status()
        self._spinner_mode = "responding"
        self._note_activity()
        self._token_estimate += max(1, len(delta) // 4)
        self._markdown_buffer += delta
        completed, remainder = split_markdown_blocks(self._markdown_buffer)
        self._markdown_buffer = remainder
        for block in completed:
            self._print_markdown_block(block)
        # auto_refresh paints; do not call refresh() on every delta.

    def on_tool_call(self, event: ToolCallEvent) -> None:
        """Flush buffered text, track pending `●` card, and update spinner."""
        self._clear_retry_status()
        self._flush_markdown_buffer()
        self._note_activity()
        self._spinner_mode = "tool_use"
        self._active_tool_name = event.name
        if event.name == "todo":
            # Checklist UI comes from todo.updated, not a collapsed tool row.
            self._pending_tools[event.call_id] = (event.name, "", "")
            return

        display_args = format_display_args(event.args_preview, tool_name=event.name)
        summary = sanitize_display_text(event.summary.strip())
        if event.connector:
            label = connector_row_label(event)
            self._connector_rows[event.call_id] = (label, event.reason)
            summary = f"{label}  {display_args}".rstrip()
        self._pending_tools[event.call_id] = (event.name, display_args, summary)

        if self.plain or not self.console.is_interactive:
            self._printed_pending_ids.add(event.call_id)
            self.console.print(
                self._build_pending_tool_line(event.name, display_args, summary),
                overflow="ellipsis",
                no_wrap=True,
            )
            self._last_printed_kind = "tool"

    def on_tool_result(self, event: ToolResultEvent) -> None:
        """Print a single-line `✓` or `✗` tool card with args, duration, and summary."""
        tool_name, display_args, _pending_summary = self._pending_tools.pop(
            event.call_id,
            ("tool", "", ""),
        )
        was_printed_pending = event.call_id in self._printed_pending_ids
        self._printed_pending_ids.discard(event.call_id)
        self._note_activity()

        self._tool_count += 1
        if self._pending_tools:
            next_tool, _next_args, _next_summary = next(
                iter(self._pending_tools.values())
            )
            self._spinner_mode = "tool_use"
            self._active_tool_name = next_tool
        else:
            self._spinner_mode = "requesting"
            self._active_tool_name = ""

        if tool_name == "todo":
            # Checklist UI is driven by `todo.updated`, not this row.
            return

        duration_ms = event.duration_ms
        summary = sanitize_display_text(clean_tool_summary(tool_name, event.summary))
        label = human_tool_label(tool_name)
        connector_row = self._connector_rows.pop(event.call_id, None)
        if connector_row is not None:
            label, reason = connector_row
            if reason and event.ok:
                summary = f"auto · {reason}" + (f" · {summary}" if summary else "")
        include_args = bool(display_args) and not was_printed_pending
        # Hide sub-1s noise (`0.0s`) from the permanent row.
        show_duration = duration_ms >= 1000
        duration = format_duration_ms(duration_ms) if show_duration else ""

        if event.ok:
            if summary and duration:
                detail = f"{duration} · {summary}"
            else:
                detail = summary or duration
            card = Text("  ✓ ", style="ok", no_wrap=True, overflow="ellipsis")
            card.append(label, style="muted")
            if include_args:
                avail = max(
                    12,
                    self.console.width - len("  ✓ ") - len(label) - len(detail) - 4,
                )
                capped_args = _cap_text_width(display_args, avail)
                card.append(f"  {capped_args}", style="muted")
            if detail:
                card.append(f"  {detail}", style="muted")
            self.console.print(card, overflow="ellipsis", no_wrap=True)
        else:
            reason = summary or "failed"
            # Fail row: glyph + label, then ⎿ reason.
            card = Text("  ✗ ", style="err", no_wrap=True, overflow="ellipsis")
            card.append(label, style="err")
            if include_args:
                avail = max(
                    12,
                    self.console.width - len("  ✗ ") - len(label) - 4,
                )
                capped_args = _cap_text_width(display_args, avail)
                card.append(f"  {capped_args}", style="muted")
            self.console.print(card, overflow="ellipsis", no_wrap=True)
            fail_mark = "  | " if self.plain else "  ⎿ "
            self.console.print(
                Text(
                    f"{fail_mark}{reason}",
                    style="err",
                    no_wrap=True,
                    overflow="ellipsis",
                ),
                overflow="ellipsis",
                no_wrap=True,
            )
            self._last_printed_kind = "tool"
            return

        if self._chat.verbose and event.preview.strip():
            shown = sanitize_display_text(event.preview)
            for line in shown.splitlines()[:12]:
                self.console.print(Text(f"    {line}", style="muted"))
        self._last_printed_kind = "tool"

    def on_status(self, message: str) -> None:
        """Update one in-place retry line (Live) instead of stacking permanent lines."""
        self._note_activity()
        self._retry_status = message
        # Interactive Live paints `_retry_status` via the spinner line.
        # Plain / non-TTY: keep latest in memory only (no stacked permanent lines).
        if not self.plain and self.console.is_interactive:
            self._refresh_live()

    def pause_for_approval(self, event: ApprovalEvent) -> None:
        """Stop turn `Live` and show the transient approval box."""
        self._flush_markdown_buffer()
        self._chat.todo_view.freeze()
        self._spinner_mode = "waiting"
        self._stop_live()
        self._approval_event = event
        self._approval_selected = 0
        self._approval_expanded = False
        if not self.plain and self.console.is_interactive:
            self._chat.ring_bell()
            panel = self._chat.build_approval_panel(
                event, selected=self._approval_selected
            )
            self._approval_live = Live(
                panel,
                console=self.console,
                transient=True,
                auto_refresh=False,
            )
            self._approval_live.start(refresh=True)
        else:
            self._chat.render_approval_panel(event)

    def expand_approval_panel(self, event: ApprovalEvent) -> None:
        """Expand a folded 24-line diff panel in place on `Ctrl+O`."""
        self._approval_expanded = True
        panel = self._chat.build_approval_panel(
            event,
            expanded=True,
            selected=getattr(self, "_approval_selected", 0),
        )
        if self._approval_live is not None:
            self._approval_live.update(panel, refresh=True)
        else:
            self.console.print(panel)

    def approval_option_count(self) -> int:
        """Return how many chooser rows the current approval panel shows."""
        event = self._approval_event
        if event is None:
            return 2
        return 3 if event.grantable else 2

    def set_approval_selection(self, selected: int) -> None:
        """Move the ❯ highlight in the live approval chooser."""
        event = self._approval_event
        if event is None:
            return
        n = self.approval_option_count()
        self._approval_selected = selected % n if n else 0
        panel = self._chat.build_approval_panel(
            event,
            expanded=self._approval_expanded,
            selected=self._approval_selected,
        )
        if self._approval_live is not None:
            self._approval_live.update(panel, refresh=True)

    @property
    def approval_selected(self) -> int:
        """Current chooser index (0=No, 1=Yes, 2=session when grantable)."""
        return self._approval_selected

    @property
    def has_printed_text(self) -> bool:
        """True once any assistant text was flushed this turn."""
        return self._has_printed_text

    @property
    def has_assistant_output(self) -> bool:
        """True if any assistant text was received (flushed or still buffered)."""
        return self._has_printed_text or bool(self._markdown_buffer.strip())

    def collapse_approval_panel(self) -> None:
        """Hide the approval Live without restarting the turn spinner."""
        self._stop_approval_live()

    def resolve_approval(
        self,
        event: ApprovalEvent,
        answer: ApprovalAnswer,
        *,
        reason: str | None = None,
        resume: bool = True,
    ) -> None:
        """Collapse the approval box into one result row; optionally resume Live."""
        self._stop_approval_live()
        target = sanitize_display_text(_approval_record_target(event))
        if answer == "yes":
            line = Text("  ✓ approved", style="ok", no_wrap=True, overflow="ellipsis")
        elif answer == "yes_session":
            line = Text(
                "  ✓ approved for session",
                style="ok",
                no_wrap=True,
                overflow="ellipsis",
            )
        else:
            line = Text("  ✗ declined", style="warn", no_wrap=True, overflow="ellipsis")
            if reason:
                clean_reason = sanitize_display_text(reason)
                line.append(f" (you said: {clean_reason})", style="muted")
        if target:
            line.append(f"  {target}", style="muted")
        self.console.print(line, overflow="ellipsis", no_wrap=True)
        self._last_printed_kind = "tool"
        if resume:
            self.resume_after_approval()

    def resume_after_approval(self) -> None:
        """Restart `Live` after the user answers the approval prompt."""
        self._stop_approval_live()
        self._ensure_live()

    def finish_done(self, event: DoneEvent) -> int:
        """Stop `Live`, flush final markdown, print end line, and return tokens."""
        self._stop_live()
        self._chat.todo_view.freeze()
        elapsed = format_elapsed_s(self.elapsed_seconds())
        tools = _tool_word(self._tool_count)
        total_tokens = (
            event.usage.input_tokens + event.usage.output_tokens
            if event.usage is not None
            else 0
        )

        if event.reason == "stopped":
            self._flush_markdown_buffer()
            # Muted interrupt trail; tell the user what to do next.
            mark = "  | " if self.plain else "  ⎿ "
            self.console.print(
                Text(
                    f"{mark}Interrupted · tell Aldwin what to do instead",
                    style="muted",
                )
            )
            self._chat.render_turn_separator()
            return total_tokens

        self._flush_markdown_buffer()

        if event.reason == "max_steps":
            steps = _steps_phrase(event.max_steps)
            if self.plain:
                self.console.print(MAX_STEPS_NOTICE.format(steps=steps))
            else:
                notice = Text(f"Stopped {steps}. Say ", style="warn")
                notice.append("continue", style="italic")
                notice.append(" to keep going.")
                self.console.print(notice)
            self._chat.render_turn_separator()
            return total_tokens

        # End-of-turn summary: ✻ Worked for …
        glyph = "*" if self.plain else "✻"
        end_line = Text(f"  {glyph} ", style="accent")
        summary = f"Worked for {elapsed}"
        if self._tool_count:
            summary += f" · {tools}"
        if event.usage is not None:
            summary += f" · {format_tokens(total_tokens)} tokens"
        end_line.append(summary, style="muted")
        self.console.print(end_line)
        if not self.plain and self.elapsed_seconds() >= 30.0:
            self._chat.ring_bell()
        self._chat.render_turn_separator()
        return total_tokens

    def finish_error(self, event: ErrorEvent) -> None:
        """Stop `Live`, flush partial output, and print the error line."""
        self._flush_markdown_buffer()
        self._stop_live()
        self._chat.todo_view.freeze()
        self._chat.render_error(event.code, event.message)
        self._chat.render_turn_separator()
