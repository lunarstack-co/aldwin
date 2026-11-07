"""Shared chooser and action menu for `/search` and `/model` provider screens."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence

from prompt_toolkit.input import Input
from rich.console import Console
from rich.text import Text

from aldwin.cli.picker import Row, pick_index, row_text

AskFn = Callable[[str], Awaitable[str]]


async def ask_line(ask: Callable[[str], object], prompt_text: str) -> str:
    """Call a prompt function that may or may not be async; Esc/Ctrl-C give ""."""
    try:
        result = ask(prompt_text)
        if hasattr(result, "__await__"):
            result = await result
    except (EOFError, KeyboardInterrupt):
        return ""
    return str(result)


async def choose(
    rows: Sequence[Row],
    *,
    console: Console,
    ask: Callable[[str], object],
    use_picker: bool,
    input_dev: Input | None,
    plain: bool,
    default: int = 0,
    disabled: Iterable[int] = (),
    header: str | None = None,
    cancel_hint: str = "Enter to cancel",
) -> int | None:
    """Return the chosen row index, or None when the user backs out.

    Interactive terminals get the arrow-key picker; plain mode and pipes get a
    numbered list read through ``ask``.
    """
    disabled_set = set(disabled)
    if use_picker:
        return await pick_index(
            rows,
            default=default,
            disabled=disabled_set,
            header=header,
            input_dev=input_dev,
            plain=plain,
        )
    if header:
        console.print(Text(f"  {header}", style="bold"))
    numbered: dict[int, int] = {}
    for idx, row in enumerate(rows):
        if idx in disabled_set:
            console.print(Text(f"  {row_text(row)}", style="bold"))
            continue
        numbered[len(numbered) + 1] = idx
        console.print(Text(f"    {len(numbered)}. {row_text(row)}"))
    while True:
        raw = (
            await ask_line(ask, f"Choose [1-{len(numbered)}, or {cancel_hint}]: ")
        ).strip()
        if not raw:
            return None
        if raw.isdigit() and int(raw) in numbered:
            return numbered[int(raw)]
        console.print(
            Text(
                f"Invalid selection {raw!r}. Enter a number from 1 to {len(numbered)}.",
                style="err",
            )
        )


async def pick_action(
    title: str,
    actions: Sequence[str],
    *,
    console: Console,
    ask: Callable[[str], object],
    use_picker: bool,
    input_dev: Input | None,
    plain: bool,
) -> str | None:
    """Show what can be done with one provider; return the action or None."""
    idx = await choose(
        [*actions, "Back"],
        console=console,
        ask=ask,
        use_picker=use_picker,
        input_dev=input_dev,
        plain=plain,
        header=title,
        cancel_hint="Enter to go back",
    )
    if idx is None or idx >= len(actions):
        return None
    return actions[idx]


async def confirm(ask: Callable[[str], object], question: str) -> bool:
    """Ask a yes/no question that defaults to No."""
    answer = (await ask_line(ask, f"{question} [y/N]: ")).strip().lower()
    return answer in ("y", "yes")
