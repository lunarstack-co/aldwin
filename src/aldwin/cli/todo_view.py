"""Live checklist rendering for `todo.updated` events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from rich.console import Group, RenderableType
from rich.live import Live
from rich.text import Text

from aldwin.client import sanitize_display_text
from aldwin.protocol import TodoItem, TodoStatus, TodoUpdatedEvent

UNICODE_MARKERS: Final[dict[TodoStatus, str]] = {
    "done": "☑",
    "in_progress": "▶",
    "pending": "☐",
    "dropped": "✘",
}
ASCII_MARKERS: Final[dict[TodoStatus, str]] = {
    "done": "[x]",
    "in_progress": "[>]",
    "pending": "[ ]",
    "dropped": "[-]",
}


@dataclass
class TodoViewState:
    """Snapshot of the session checklist for rendering."""

    items: list[TodoItem] = field(default_factory=list)
    done: int = 0
    total: int = 0

    @classmethod
    def from_event(cls, event: TodoUpdatedEvent) -> TodoViewState:
        """Build state from a protocol `todo.updated` event."""
        return cls(items=list(event.items), done=event.done, total=event.total)

    @property
    def is_complete(self) -> bool:
        """True when every item is done or dropped (or the list is empty)."""
        if self.total == 0:
            return True
        return all(item.status in ("done", "dropped") for item in self.items)

    @property
    def dropped_count(self) -> int:
        return sum(1 for item in self.items if item.status == "dropped")


def render_collapsed(state: TodoViewState, *, plain: bool = False) -> Text:
    """One-line collapse: ``Tasks · N done`` (+ dropped when any)."""
    dropped = state.dropped_count
    done_only = state.done - dropped
    if dropped:
        line = f"Tasks · {done_only} done, {dropped} dropped"
    else:
        line = f"Tasks · {state.done} done"
    if plain:
        return Text(line)
    return Text(line, style="muted")


def render_todo_block(
    state: TodoViewState,
    *,
    plain: bool = False,
    unicode: bool = True,
) -> RenderableType:
    """Render the full checklist (or the collapse line when complete)."""
    if state.total == 0:
        return Text("")
    if state.is_complete:
        return render_collapsed(state, plain=plain)

    use_unicode = unicode and not plain
    markers = UNICODE_MARKERS if use_unicode else ASCII_MARKERS
    lines: list[Text] = []
    if plain:
        header = Text(f"Tasks {state.done}/{state.total}")
    else:
        header = Text(f"Tasks {state.done}/{state.total}", style="muted")
    lines.append(header)

    for item in state.items:
        marker = markers.get(item.status, markers["pending"])
        clean = sanitize_display_text(item.text)
        row = Text("  ")
        if item.status == "done":
            # Done: filled accent checkmark, strikethrough, reduced opacity.
            if plain:
                row.append(f"{marker} ")
                row.append(clean)
            elif use_unicode:
                row.append(f"{marker} ", style="accent dim")
                row.append(clean, style="muted strike dim")
            else:
                row.append(f"{marker} ", style="accent dim")
                row.append(clean, style="muted dim")
        elif item.status == "in_progress":
            if plain:
                row.append(f"{marker} ")
                row.append(clean)
            else:
                row.append(f"{marker} ", style="accent")
                row.append(clean, style="bold")
        elif item.status == "dropped":
            if plain:
                row.append(f"{marker} ")
                row.append(clean)
            else:
                row.append(f"{marker} ", style="muted")
                row.append(clean, style="muted")
        else:
            row.append(f"{marker} ")
            row.append(clean)
        lines.append(row)

    return Group(*lines)


@dataclass
class TodoView:
    """Tracks todo state and redraws in place within a turn."""

    plain: bool = False
    unicode: bool = True
    state: TodoViewState | None = None
    _printed_this_turn: bool = False
    _live: Live | None = None
    _console: object | None = None

    def reset_turn(self) -> None:
        """Call at the start of each turn so the next update prints a fresh block."""
        self.freeze()
        self._printed_this_turn = False

    def clear(self) -> None:
        """Drop checklist state (e.g. after `/new`)."""
        self.freeze()
        self.state = None
        self._printed_this_turn = False

    def freeze(self) -> None:
        """Stop any live redraw but keep the last checklist state for `/todo`."""
        if self._live is not None:
            self._live.stop()
            self._live = None

    @property
    def live_active(self) -> bool:
        """True while a Rich Live region is redrawing the checklist."""
        return self._live is not None

    def apply_event(self, event: TodoUpdatedEvent, console: object) -> None:
        """Update from `todo.updated` and print or redraw the block.

        Callers must pause any other Rich ``Live`` (e.g. the turn spinner)
        before this method so only one Live region is active.
        """
        from rich.console import Console

        assert isinstance(console, Console)
        self.state = TodoViewState.from_event(event)
        renderable = render_todo_block(
            self.state,
            plain=self.plain,
            unicode=self.unicode,
        )
        if self.state.total == 0:
            self.freeze()
            return

        if self.state.is_complete:
            self.freeze()
            console.print(renderable)
            self._printed_this_turn = True
            return

        use_live = not self.plain and console.is_interactive
        if not self._printed_this_turn:
            if use_live:
                self._live = Live(
                    renderable,
                    console=console,
                    refresh_per_second=8,
                    transient=False,
                )
                self._live.start()
            else:
                console.print(renderable)
            self._printed_this_turn = True
            return

        if self._live is not None:
            self._live.update(renderable)
        else:
            console.print(renderable)

    def toolbar_label(self) -> str | None:
        """Return ``Tasks n/m`` while unfinished, else ``None``."""
        if self.state is None or self.state.total == 0 or self.state.is_complete:
            return None
        return f"Tasks {self.state.done}/{self.state.total}"

    def print_current(self, console: object) -> None:
        """Print the current list for `/todo` (or the empty message)."""
        from rich.console import Console

        assert isinstance(console, Console)
        if self.state is None or self.state.total == 0:
            console.print(Text("No task list in this session", style="muted"))
            return
        console.print(
            render_todo_block(
                self.state,
                plain=self.plain,
                unicode=self.unicode,
            )
        )


__all__ = [
    "TodoView",
    "TodoViewState",
    "render_collapsed",
    "render_todo_block",
]
