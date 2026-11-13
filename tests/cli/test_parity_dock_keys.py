"""Drive dock key bindings (paste collapse, newline, Ctrl-C clear) via PipeInput."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aldwin.cli.dock import AldwinPromptSession
from aldwin.cli.paste import PasteStore


@pytest.mark.asyncio
async def test_dock_bracketed_paste_collapses_large_paste() -> None:
    store = PasteStore()
    with create_pipe_input() as pipe:
        session = AldwinPromptSession(
            input=pipe,
            output=DummyOutput(),
            paste_store=store,
        )
        session.key_bindings = session.attach_key_bindings()
        body = "one\ntwo\nthree\nfour"
        # Bracketed paste sequence.
        pipe.send_text(f"\x1b[200~{body}\x1b[201~")
        pipe.send_text("\r")

        result = await asyncio.wait_for(
            session.prompt_async("> "),
            timeout=2.0,
        )
        assert "Pasted text #" in result
        assert store.resolve(result) == body


@pytest.mark.asyncio
async def test_dock_ctrl_j_inserts_newline_then_submit() -> None:
    with create_pipe_input() as pipe:
        session = AldwinPromptSession(input=pipe, output=DummyOutput())
        session.key_bindings = session.attach_key_bindings()
        pipe.send_text("hello")
        pipe.send_text("\x0a")  # Ctrl-J -> newline
        pipe.send_text("world\r")
        result = await asyncio.wait_for(session.prompt_async("> "), timeout=2.0)
        assert "hello\nworld" in result or result == "hello\nworld"


@pytest.mark.asyncio
async def test_dock_double_esc_clears_draft() -> None:
    with create_pipe_input() as pipe:
        session = AldwinPromptSession(input=pipe, output=DummyOutput())
        session.key_bindings = session.attach_key_bindings()
        pipe.send_text("draft")
        pipe.send_text("\x1b")
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b")
        await asyncio.sleep(0.05)
        pipe.send_text("ok\r")
        result = await asyncio.wait_for(session.prompt_async("> "), timeout=2.0)
        assert "ok" in result


@pytest.mark.asyncio
async def test_dock_ctrl_c_clears_draft_then_eof_quits() -> None:
    quits: list[bool] = []

    def on_quit() -> None:
        quits.append(True)

    with create_pipe_input() as pipe:
        session = AldwinPromptSession(
            input=pipe,
            output=DummyOutput(),
            on_quit=on_quit,
        )
        session.key_bindings = session.attach_key_bindings()
        pipe.send_text("draft")
        pipe.send_text("\x03")  # clear draft
        pipe.send_text("\x03")  # first empty ctrl-c
        pipe.send_text("\x03")  # second empty ctrl-c -> quit
        with pytest.raises(EOFError):
            await asyncio.wait_for(session.prompt_async("> "), timeout=2.0)
        assert quits == [True]


@pytest.mark.asyncio
async def test_dock_ctrl_g_opens_editor_and_returns_edited_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_open_editor(self: Buffer, filename: str) -> bool:
        Path(filename).write_text("edited draft", encoding="utf-8")
        return True

    original_open = Buffer.open_in_editor
    editor_task: list[asyncio.Task[None]] = []
    editor_started = asyncio.Event()

    def track_editor_task(
        self: Buffer, validate_and_handle: bool = False
    ) -> asyncio.Task:
        task = original_open(self, validate_and_handle)
        editor_task.append(task)
        editor_started.set()
        return task

    monkeypatch.setattr(Buffer, "_open_file_in_editor", fake_open_editor)
    monkeypatch.setattr(Buffer, "open_in_editor", track_editor_task)

    with create_pipe_input() as pipe:
        session = AldwinPromptSession(input=pipe, output=DummyOutput())
        session.key_bindings = session.attach_key_bindings()

        pipe.send_text("original draft")
        pipe.send_text("\x07")

        prompt_task = asyncio.create_task(session.prompt_async("> "))

        await asyncio.wait_for(editor_started.wait(), timeout=2.0)
        await asyncio.wait_for(editor_task[0], timeout=2.0)

        pipe.send_text("\r")

        result = await asyncio.wait_for(prompt_task, timeout=2.0)

        assert result == "edited draft"


@pytest.mark.asyncio
async def test_dock_ctrl_g_preserves_draft_when_editor_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = Buffer.open_in_editor
    editor_task: list[asyncio.Task[None]] = []
    editor_started = asyncio.Event()

    def fake_open_editor(self: Buffer, filename: str) -> bool:
        return False

    def track_editor_task(
        self: Buffer,
        validate_and_handle: bool = False,
    ) -> asyncio.Task[None]:
        task = original_open(self, validate_and_handle)
        editor_task.append(task)
        editor_started.set()
        return task

    monkeypatch.setattr(Buffer, "_open_file_in_editor", fake_open_editor)
    monkeypatch.setattr(Buffer, "open_in_editor", track_editor_task)

    with create_pipe_input() as pipe:
        session = AldwinPromptSession(input=pipe, output=DummyOutput())
        session.key_bindings = session.attach_key_bindings()

        pipe.send_text("original draft")
        pipe.send_text("\x07")  # Ctrl+G

        prompt_task = asyncio.create_task(session.prompt_async("> "))

        await asyncio.wait_for(editor_started.wait(), timeout=2.0)
        await asyncio.wait_for(editor_task[0], timeout=2.0)

        pipe.send_text("\r")
        result = await asyncio.wait_for(prompt_task, timeout=2.0)

        assert result == "original draft"


@pytest.mark.asyncio
async def test_dock_ctrl_g_does_nothing_while_completion_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor_called = False

    def fake_open_editor(self: Buffer) -> asyncio.Task[None]:
        nonlocal editor_called
        editor_called = True
        raise AssertionError("Editor should not open")

    monkeypatch.setattr(Buffer, "open_in_editor", fake_open_editor)

    with create_pipe_input() as pipe:
        session = AldwinPromptSession(input=pipe, output=DummyOutput())
        session.key_bindings = session.attach_key_bindings()

        prompt_task = asyncio.create_task(session.prompt_async("> "))

        await asyncio.sleep(0.05)

        # Simulate an active completion picker.
        session.default_buffer._set_completions([])

        pipe.send_text("\x07")  # Ctrl+G
        await asyncio.sleep(0.05)

        assert not editor_called

        session.default_buffer.cancel_completion()
        pipe.send_text("\r")

        await asyncio.wait_for(prompt_task, timeout=2.0)
