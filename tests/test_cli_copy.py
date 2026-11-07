"""`/copy`: the last answer or its last code block, onto the clipboard."""

from __future__ import annotations

import base64
import io
import subprocess
from typing import Any, cast

import pytest
from rich.console import Console

from aldwin.cli import clipboard, commands
from aldwin.cli.clipboard import (
    MAX_OSC52_BYTES,
    copy_to_clipboard,
    osc52_sequence,
    osc52_supported,
)
from aldwin.cli.commands import (
    SLASH_COMMANDS,
    SlashCommandClient,
    handle_slash_command,
)
from aldwin.cli.render import ChatRenderer, last_code_block
from aldwin.cli.theme import THEME
from aldwin.protocol import DoneEvent, ToolCallEvent

OSC52_ENV = {"TERM": "xterm-256color"}
ANSWER = "Here you go:\n\n```python\ndef add(a, b):\n    return a + b\n```\n\nDone."


class FakeRun:
    """Stands in for `subprocess.run`; fails the programs named in `failing`."""

    def __init__(self, failing: tuple[str, ...] = ()) -> None:
        self.failing = failing
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(
        self, argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((argv, kwargs))
        code = 1 if any(argv[0].endswith(name) for name in self.failing) else 0
        return subprocess.CompletedProcess(argv, code)


def _which_all(name: str) -> str | None:
    return f"/usr/bin/{name}"


def _which_none(name: str) -> str | None:
    return None


def _renderer() -> tuple[ChatRenderer, io.StringIO]:
    out = io.StringIO()
    console = Console(
        file=out, width=80, force_terminal=False, color_system=None, theme=THEME
    )
    return ChatRenderer(console, plain=True), out


def _done(reason: Any = "answered") -> DoneEvent:
    return DoneEvent(re="r1", reason=reason)


# Picking the last answer


def test_finished_answer_is_kept_as_written() -> None:
    chat, _ = _renderer()
    turn = chat.start_turn()
    for delta in (
        "Here you go:\n\n```python\n",
        "def add(a, b):\n    retu",
        "rn a + b\n```\n",
    ):
        turn.on_text(delta)
    turn.finish_done(_done())
    assert (
        chat.last_answer
        == "Here you go:\n\n```python\ndef add(a, b):\n    return a + b\n```"
    )


def test_text_around_tool_calls_is_joined_into_one_answer() -> None:
    chat, _ = _renderer()
    turn = chat.start_turn()
    turn.on_text("Let me check.")
    turn.on_tool_call(ToolCallEvent(re="r1", call_id="c1", name="read_file"))
    turn.on_text("The file says hello.")
    turn.finish_done(_done())
    assert chat.last_answer == "Let me check.\n\nThe file says hello."


def test_later_answer_replaces_earlier_but_stopped_one_does_not() -> None:
    chat, _ = _renderer()
    first = chat.start_turn()
    first.on_text("first")
    first.finish_done(_done())
    second = chat.start_turn()
    second.on_text("second")
    second.finish_done(_done("max_steps"))
    assert chat.last_answer == "second"

    stopped = chat.start_turn()
    stopped.on_text("half an ans")
    stopped.finish_done(_done("stopped"))
    assert chat.last_answer == "second"


def test_turn_without_text_keeps_the_previous_answer() -> None:
    chat, _ = _renderer()
    turn = chat.start_turn()
    turn.on_text("kept")
    turn.finish_done(_done())
    silent = chat.start_turn()
    silent.finish_done(_done())
    assert chat.last_answer == "kept"


# Picking the last code block


def test_last_code_block_picks_the_last_one() -> None:
    text = "```\nfirst\n```\n\nthen\n\n~~~sh\necho one\n\necho two\n~~~\n\nend"
    assert last_code_block(text) == "echo one\n\necho two"


def test_last_code_block_keeps_inner_indentation_and_drops_fence_indent() -> None:
    text = "1. Run:\n\n   ```python\n   if x:\n       go()\n   ```\n"
    assert last_code_block(text) == "if x:\n    go()"


def test_last_code_block_handles_longer_fences_and_unclosed_blocks() -> None:
    nested = "````md\n```\ninner\n```\n````"
    assert last_code_block(nested) == "```\ninner\n```"
    assert last_code_block("text\n```\nstill open") == "still open"


@pytest.mark.parametrize("text", ["no code here", "```\n```", "```\n   \n```"])
def test_last_code_block_none_when_missing_or_empty(text: str) -> None:
    assert last_code_block(text) is None


# OSC 52 and the 100 KB cap


def test_osc52_sequence_encodes_utf8() -> None:
    sequence = osc52_sequence("héllo ✓")
    assert sequence is not None
    assert sequence.startswith("\x1b]52;c;") and sequence.endswith("\x07")
    payload = sequence[len("\x1b]52;c;") : -1]
    assert base64.b64decode(payload).decode("utf-8") == "héllo ✓"


def test_osc52_cap_is_100_kb_of_encoded_text() -> None:
    fits = "a" * (MAX_OSC52_BYTES // 4 * 3)
    too_big = fits + "aaa"
    assert MAX_OSC52_BYTES == 100 * 1024
    assert osc52_sequence(fits) is not None
    assert osc52_sequence(too_big) is None


def test_text_over_the_cap_goes_to_the_fallback() -> None:
    out, run = io.StringIO(), FakeRun()
    method = copy_to_clipboard(
        "a" * MAX_OSC52_BYTES,
        stream=out,
        is_terminal=True,
        environ=OSC52_ENV,
        platform="darwin",
        which=_which_all,
        run=run,
    )
    assert method == "pbcopy"
    assert out.getvalue() == ""


@pytest.mark.parametrize(
    ("is_terminal", "environ", "expected"),
    [
        (True, {"TERM": "xterm-kitty"}, True),
        (False, {"TERM": "xterm-kitty"}, False),
        (True, {"TERM": "dumb"}, False),
        (True, {}, False),
        (True, {"TERM": "xterm", "TERM_PROGRAM": "Apple_Terminal"}, False),
        (True, {"TERM": "xterm", "VTE_VERSION": "7600"}, False),
        (True, {"TERM": "xterm", "VTE_VERSION": "7600", "SSH_CONNECTION": "x"}, True),
    ],
    ids=["kitty", "not-a-tty", "dumb", "no-term", "apple", "vte", "vte-over-ssh"],
)
def test_osc52_supported(
    is_terminal: bool, environ: dict[str, str], expected: bool
) -> None:
    assert osc52_supported(is_terminal=is_terminal, environ=environ) is expected


def test_osc52_is_used_first_and_nothing_is_run() -> None:
    out, run = io.StringIO(), FakeRun()
    method = copy_to_clipboard(
        "hi",
        stream=out,
        is_terminal=True,
        environ=OSC52_ENV,
        platform="linux",
        which=_which_all,
        run=run,
    )
    assert method == "osc52"
    assert out.getvalue() == osc52_sequence("hi")
    assert run.calls == []


# Fallback order


def test_macos_falls_back_to_pbcopy_with_a_fixed_argv() -> None:
    run = FakeRun()
    method = copy_to_clipboard(
        "héllo",
        stream=io.StringIO(),
        is_terminal=False,
        environ={"PATH": "/usr/bin", "SECRET_TOKEN": "x"},
        platform="darwin",
        which=_which_all,
        run=run,
    )
    assert method == "pbcopy"
    argv, kwargs = run.calls[0]
    assert argv == ["/usr/bin/pbcopy"]
    assert kwargs["input"] == "héllo".encode()
    assert "shell" not in kwargs
    assert "SECRET_TOKEN" not in kwargs["env"]
    assert kwargs["env"]["LC_CTYPE"] == "UTF-8"


def test_linux_tries_wl_copy_then_xclip() -> None:
    run = FakeRun(failing=("wl-copy",))
    method = copy_to_clipboard(
        "hi",
        stream=io.StringIO(),
        is_terminal=False,
        environ={"DISPLAY": ":0"},
        platform="linux",
        which=_which_all,
        run=run,
    )
    assert method == "xclip"
    assert [argv for argv, _ in run.calls] == [
        ["/usr/bin/wl-copy"],
        ["/usr/bin/xclip", "-selection", "clipboard"],
    ]
    assert run.calls[1][1]["env"]["DISPLAY"] == ":0"


def test_missing_programs_are_skipped() -> None:
    run = FakeRun()
    installed = {"xclip": "/opt/bin/xclip"}
    method = copy_to_clipboard(
        "hi",
        stream=io.StringIO(),
        is_terminal=False,
        environ={},
        platform="linux",
        which=installed.get,
        run=run,
    )
    assert method == "xclip"
    assert [argv[0] for argv, _ in run.calls] == ["/opt/bin/xclip"]


def test_nothing_works_returns_none() -> None:
    def explode(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(argv, 5)

    assert (
        copy_to_clipboard(
            "hi",
            stream=io.StringIO(),
            is_terminal=False,
            environ={},
            platform="linux",
            which=_which_all,
            run=explode,
        )
        is None
    )
    assert (
        copy_to_clipboard(
            "hi",
            stream=io.StringIO(),
            is_terminal=False,
            environ={},
            platform="darwin",
            which=_which_none,
        )
        is None
    )


# The /copy command


async def _run(line: str, chat: ChatRenderer) -> None:
    async def no_prompt(_: str) -> str:
        return ""

    await handle_slash_command(
        line,
        client=cast(SlashCommandClient, object()),
        renderer=chat,
        prompt_line=no_prompt,
    )


@pytest.fixture
def copied(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    texts: list[str] = []

    def fake_copy(text: str, **kwargs: Any) -> str:
        texts.append(text)
        return "osc52"

    monkeypatch.setattr(commands, "copy_to_clipboard", fake_copy)
    return texts


async def test_copy_with_nothing_yet(copied: list[str]) -> None:
    chat, out = _renderer()
    await _run("/copy", chat)
    await _run("/copy code", chat)
    assert out.getvalue().splitlines() == [commands.COPY_NOTHING] * 2
    assert copied == []


async def test_copy_answer_and_code(copied: list[str]) -> None:
    chat, out = _renderer()
    chat.last_answer = ANSWER
    await _run("/copy", chat)
    await _run("/COPY code", chat)
    assert copied == [ANSWER, "def add(a, b):\n    return a + b"]
    assert out.getvalue().splitlines() == ["✓ Copied 8 lines", "✓ Copied 2 lines"]


async def test_copy_code_without_a_code_block(copied: list[str]) -> None:
    chat, out = _renderer()
    chat.last_answer = "Just words."
    await _run("/copy code", chat)
    await _run("/copy", chat)
    assert out.getvalue().splitlines() == [commands.COPY_NO_CODE, "✓ Copied 1 line"]


async def test_copy_bad_argument(copied: list[str]) -> None:
    chat, out = _renderer()
    chat.last_answer = ANSWER
    await _run("/copy everything", chat)
    assert out.getvalue().strip() == commands.COPY_USAGE
    assert copied == []


async def test_copy_when_no_clipboard_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard, "fallback_commands", lambda platform: [])
    chat, out = _renderer()
    chat.last_answer = ANSWER
    await _run("/copy", chat)
    assert out.getvalue().strip() == commands.COPY_FAILED


async def test_copy_is_listed_in_help() -> None:
    assert "/copy" in [cmd for cmd, _ in SLASH_COMMANDS]
    chat, out = _renderer()
    await _run("/help", chat)
    assert "/copy" in out.getvalue()


class BrokenStream(io.StringIO):
    def write(self, s: str) -> int:
        raise OSError("terminal went away")


def test_failed_osc52_write_falls_back() -> None:
    run = FakeRun()
    method = copy_to_clipboard(
        "hi",
        stream=BrokenStream(),
        is_terminal=True,
        environ=OSC52_ENV,
        platform="darwin",
        which=_which_all,
        run=run,
    )
    assert method == "pbcopy"
