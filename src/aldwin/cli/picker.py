"""Shared inline ↑/↓ Enter Esc list for /model and similar choosers."""

from __future__ import annotations

from collections.abc import Sequence

from prompt_toolkit.application import Application
from prompt_toolkit.input import Input, PipeInput
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import DummyOutput

from aldwin.cli.theme import PICKER_STYLE, PLAIN_PROMPT_STYLE

# A row is plain text, or `(style, text)` fragments such as `("class:muted", "…")`.
Row = str | Sequence[tuple[str, str]]


def row_text(row: Row) -> str:
    """A row without its styles, for numbered lists and plain output."""
    return row if isinstance(row, str) else "".join(text for _style, text in row)


async def pick_index(
    items: Sequence[Row],
    *,
    default: int = 0,
    input_dev: Input | None = None,
    plain: bool = False,
    header: str | None = None,
    erase_when_done: bool = False,
) -> int | None:
    """Show an inline ❯ list; return selected index, or ``None`` on Esc.

    Uses a minimal prompt_toolkit Application so tests can drive it via PipeInput.
    PipeInput tests use DummyOutput; real TTYs keep the normal terminal output.
    """
    if not items:
        return None
    selected = max(0, min(default, len(items) - 1))
    result: dict[str, int | None] = {"value": None}

    def _render() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        if header:
            fragments.append(("class:heading", f"{header}"))
            fragments.append(("class:hint", "   ↑↓ move · Enter choose · Esc back\n\n"))
        for idx, label in enumerate(items):
            chosen = idx == selected
            if plain:
                fragments.append(("", f"{'> ' if chosen else '  '}{row_text(label)}\n"))
                continue
            fragments.append(("class:accent", "❯ " if chosen else "  "))
            if isinstance(label, str):
                style = "class:accent" if chosen else "class:muted"
                fragments.append((style, label))
            else:
                for style, text in label:
                    fragments.append(
                        (f"{style} class:selected" if chosen else style, text)
                    )
            fragments.append(("", "\n"))
        return fragments

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("c-p")
    def _up(_event: KeyPressEvent) -> None:
        nonlocal selected
        selected = (selected - 1) % len(items)

    @kb.add("down")
    @kb.add("c-n")
    def _down(_event: KeyPressEvent) -> None:
        nonlocal selected
        selected = (selected + 1) % len(items)

    @kb.add("enter")
    def _enter(event: KeyPressEvent) -> None:
        result["value"] = selected
        event.app.exit()

    @kb.add("escape", eager=True)
    def _esc(event: KeyPressEvent) -> None:
        result["value"] = None
        event.app.exit()

    # Digit shortcuts 1..n
    for digit in range(1, min(9, len(items)) + 1):

        @kb.add(str(digit))
        def _digit(event: KeyPressEvent, choice: int = digit - 1) -> None:
            result["value"] = choice
            event.app.exit()

    control = FormattedTextControl(_render, show_cursor=False)  # type: ignore[arg-type]
    app: Application[None] = Application(
        layout=Layout(Window(content=control)),
        key_bindings=kb,
        full_screen=False,
        erase_when_done=erase_when_done,
        style=PLAIN_PROMPT_STYLE if plain else PICKER_STYLE,
        input=input_dev,
        output=DummyOutput() if isinstance(input_dev, PipeInput) else None,
    )
    await app.run_async()
    return result["value"]
