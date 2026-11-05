"""Shared inline ↑/↓ Enter Esc list for /model and similar choosers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

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


def _normalize_style(style: str) -> str:
    cleaned = style.strip()
    if not cleaned or cleaned.startswith("class:"):
        return cleaned
    if cleaned in (
        "muted",
        "accent",
        "heading",
        "hint",
        "selected",
        "ok",
        "warn",
        "err",
    ):
        return f"class:{cleaned}"
    if cleaned == "bold":
        return "class:heading"
    return cleaned


async def pick_index(
    items: Sequence[Row],
    *,
    default: int = 0,
    input_dev: Input | None = None,
    plain: bool = False,
    header: str | None = None,
    erase_when_done: bool = False,
    disabled: Iterable[int] = (),
) -> int | None:
    """Show an inline ❯ list; return selected index, or ``None`` on Esc.

    Uses a minimal prompt_toolkit Application so tests can drive it via PipeInput.
    PipeInput tests use DummyOutput; real TTYs keep the normal terminal output.
    """
    if not items:
        return None
    disabled_set = set(disabled)
    selectable = [i for i in range(len(items)) if i not in disabled_set]
    if not selectable:
        return None
    selected = default if default in selectable else selectable[0]
    result: dict[str, int | None] = {"value": None}

    def _render() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        if header:
            fragments.append(("class:heading", f"{header}"))
            fragments.append(("class:hint", "   ↑↓ move · Enter choose · Esc back\n\n"))
        for idx, label in enumerate(items):
            if idx in disabled_set:
                if plain:
                    fragments.append(("", f"  {row_text(label)}\n"))
                else:
                    fragments.append(("", "  "))
                    if isinstance(label, str):
                        fragments.append(("class:heading", label))
                    else:
                        for style, text in label:
                            fragments.append((_normalize_style(style), text))
                    fragments.append(("", "\n"))
                continue

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
                    norm = _normalize_style(style)
                    fragments.append(
                        (f"{norm} class:selected" if chosen else norm, text)
                    )
            fragments.append(("", "\n"))
        return fragments

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("c-p")
    def _up(_event: KeyPressEvent) -> None:
        nonlocal selected
        orig = selected
        for _ in range(len(items)):
            selected = (selected - 1) % len(items)
            if selected not in disabled_set:
                return
        selected = orig

    @kb.add("down")
    @kb.add("c-n")
    def _down(_event: KeyPressEvent) -> None:
        nonlocal selected
        orig = selected
        for _ in range(len(items)):
            selected = (selected + 1) % len(items)
            if selected not in disabled_set:
                return
        selected = orig

    @kb.add("enter")
    def _enter(event: KeyPressEvent) -> None:
        if selected not in disabled_set:
            result["value"] = selected
            event.app.exit()

    @kb.add("escape", eager=True)
    def _esc(event: KeyPressEvent) -> None:
        result["value"] = None
        event.app.exit()

    # Digit shortcuts 1..n for selectable items
    for digit in range(1, min(9, len(selectable)) + 1):
        target = selectable[digit - 1]

        @kb.add(str(digit))
        def _digit(event: KeyPressEvent, choice: int = target) -> None:
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
