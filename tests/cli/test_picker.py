"""Tests for inline picker header skipping and navigation."""

from __future__ import annotations

import asyncio

import pytest
from prompt_toolkit.input import create_pipe_input

from aldwin.cli.picker import pick_index


@pytest.mark.asyncio
async def test_pick_index_skips_disabled_headers() -> None:
    """Cursor navigation skips disabled header rows."""
    items = [
        "On this computer",  # index 0 (header)
        "qwen3:8b",  # index 1
        "OpenAI",  # index 2 (header)
        "gpt-4o",  # index 3
    ]
    disabled = {0, 2}

    with create_pipe_input() as pipe:
        task = asyncio.create_task(
            pick_index(items, default=0, disabled=disabled, input_dev=pipe, plain=False)
        )
        await asyncio.sleep(0.05)
        # Default should jump to first selectable (index 1)
        # Down keypress should skip index 2 and land on index 3
        pipe.send_text("\x1b[B\r")
        chosen = await asyncio.wait_for(task, timeout=1.0)
        assert chosen == 3


@pytest.mark.asyncio
async def test_pick_index_digit_selects_nth_selectable() -> None:
    """Digit shortcut selects the N-th selectable item, skipping headers."""
    items = [
        "Header 1",  # index 0 (header)
        "Item A",  # index 1 (selectable 1)
        "Header 2",  # index 2 (header)
        "Item B",  # index 3 (selectable 2)
    ]
    disabled = {0, 2}

    with create_pipe_input() as pipe:
        task = asyncio.create_task(
            pick_index(items, default=0, disabled=disabled, input_dev=pipe, plain=True)
        )
        await asyncio.sleep(0.05)
        # Press "2" -> should select "Item B" (index 3)
        pipe.send_text("2")
        chosen = await asyncio.wait_for(task, timeout=1.0)
        assert chosen == 3


@pytest.mark.asyncio
async def test_pick_index_interactive_styled_fragments() -> None:
    """Styled fragments with class: or bare style names render cleanly without error."""
    from aldwin.cli.picker import Row

    items: list[Row] = [
        [("class:heading", "On this computer")],
        "qwen3:8b",
        [("muted", "LM Studio · not running")],
        "gpt-4o",
    ]
    disabled = {0, 2}

    with create_pipe_input() as pipe:
        task = asyncio.create_task(
            pick_index(
                items,
                default=1,
                disabled=disabled,
                input_dev=pipe,
                plain=False,
            )
        )
        await asyncio.sleep(0.05)
        pipe.send_text("\r")
        chosen = await asyncio.wait_for(task, timeout=1.0)
        assert chosen == 1


@pytest.mark.asyncio
async def test_picker_two_step_navigation_esc_goes_back() -> None:
    """Esc on secondary model chooser returns None so caller can return to providers."""
    model_items = ["qwen3:8b", "deepseek-r1:8b"]

    with create_pipe_input() as pipe:
        task = asyncio.create_task(
            pick_index(
                model_items,
                default=0,
                header="Ollama models",
                input_dev=pipe,
                plain=False,
            )
        )
        await asyncio.sleep(0.05)
        # Send Esc
        pipe.send_text("\x1b")
        chosen = await asyncio.wait_for(task, timeout=1.0)
        assert chosen is None


@pytest.mark.asyncio
async def test_a_long_list_reaches_its_last_item_past_headings() -> None:
    """Every row stays reachable in a list taller than a terminal, headings skipped."""
    items: list[str] = []
    disabled: set[int] = set()
    for group in range(5):
        disabled.add(len(items))
        items.append(f"Heading {group}")
        items.extend(f"App {group}-{n}" for n in range(8))

    with create_pipe_input() as pipe:
        task = asyncio.create_task(
            pick_index(items, default=0, disabled=disabled, input_dev=pipe, plain=False)
        )
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b[B" * 39 + "\r")
        chosen = await asyncio.wait_for(task, timeout=2.0)
    assert chosen is not None
    assert items[chosen] == "App 4-7"
