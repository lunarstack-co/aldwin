"""Additional coverage for skills-used lines, completions, and dock Esc/?."""

from __future__ import annotations

import asyncio
from io import StringIO

import pytest
from prompt_toolkit.formatted_text import to_plain_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aldwin.cli.chat_screen import (
    _render_skills_used,
    format_bottom_toolbar,
    format_toolbar_model_segment,
)
from aldwin.cli.completions_menu import format_completion_row
from aldwin.cli.dock import AldwinPromptSession
from aldwin.cli.paste import PasteStore
from aldwin.cli.theme import create_console
from aldwin.protocol import DroppedSkill, SkillsUsedEvent


def test_toolbar_formatters() -> None:
    wide = format_bottom_toolbar(
        "gpt-4o", "sess", 1000, left_hint="? for shortcuts", width=80
    )
    assert "gpt-4o" in wide
    narrow = format_bottom_toolbar("gpt-4o", "sess", 1000, width=20)
    assert "gpt-4o" in narrow
    seg = to_plain_text(
        format_toolbar_model_segment("gpt-4o", 500, todo_label="1/2", plain=True)
    )
    assert "gpt-4o" in seg


def test_render_skills_used_and_dropped() -> None:
    buf = StringIO()
    console = create_console(plain=True, width=80, file=buf)
    _render_skills_used(
        console,
        SkillsUsedEvent(
            re="r1",
            names=["using-aldwin"],
            dropped=[
                DroppedSkill(name="gone", reason="unknown"),
                DroppedSkill(name="extra", reason="limit"),
            ],
        ),
    )
    text = buf.getvalue()
    assert "using using-aldwin" in text
    assert "isn't a skill" in text
    assert "Only 3 skills" in text


def test_format_completion_row_unselected_accent() -> None:
    row = format_completion_row(
        selected=False, name="/model", description="Switch model"
    )
    assert any("/model" in frag[1] for frag in row)
    assert any("Switch model" in frag[1] for frag in row)


@pytest.mark.asyncio
async def test_dock_question_shortcuts_then_escape() -> None:
    with create_pipe_input() as pipe:
        session = AldwinPromptSession(input=pipe, output=DummyOutput())
        session.key_bindings = session.attach_key_bindings()
        pipe.send_text("?")
        await asyncio.sleep(0.05)
        # `?` opens the shortcuts footer; Esc closes it before the submit.
        pipe.send_text("\x1b")
        pipe.send_text("hi\r")
        result = await asyncio.wait_for(session.prompt_async("> "), timeout=2.0)
        assert "hi" in result


@pytest.mark.asyncio
async def test_dock_paste_expand_second_paste() -> None:
    store = PasteStore()
    with create_pipe_input() as pipe:
        session = AldwinPromptSession(
            input=pipe,
            output=DummyOutput(),
            paste_store=store,
        )
        session.key_bindings = session.attach_key_bindings()
        body = "a\nb\nc\nd"
        pipe.send_text(f"\x1b[200~{body}\x1b[201~")
        # Second paste while footer is paste expands.
        pipe.send_text("\x1b[200~\x1b[201~")
        pipe.send_text("\r")
        result = await asyncio.wait_for(session.prompt_async("> "), timeout=2.0)
        assert body in result or "Pasted text" in result
