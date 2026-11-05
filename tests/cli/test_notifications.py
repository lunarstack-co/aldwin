"""Notification protocols, quiet modes, and renderer integration without APIs."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from canary import CANARY
from fakes.host import FakeClient
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from aldwin.cli.commands import SlashCompleter, handle_slash_command
from aldwin.cli.notifications import notify_terminal
from aldwin.cli.render import ChatRenderer, TurnRenderer
from aldwin.cli.theme import create_console
from aldwin.protocol import ApprovalEvent, DoneEvent


@pytest.fixture(autouse=True)
def terminal_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TERM_PROGRAM", "KITTY_WINDOW_ID", "KONSOLE_VERSION", "NO_COLOR"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("env", "prefix"),
    [
        ({"TERM_PROGRAM": "iTerm.app"}, "\x1b]9;"),
        ({"TERM_PROGRAM": "ghostty"}, "\x1b]9;"),
        ({"TERM_PROGRAM": "WezTerm"}, "\x1b]9;"),
        ({"TERM_PROGRAM": "kitty"}, "\x1b]9;"),
        ({"KITTY_WINDOW_ID": "1"}, "\x1b]9;"),
        ({"TERM_PROGRAM": "vscode"}, "\x1b]777;notify;Aldwin;"),
        ({"TERM_PROGRAM": "Konsole"}, "\x1b]777;notify;Aldwin;"),
        ({"KONSOLE_VERSION": "240800"}, "\x1b]777;notify;Aldwin;"),
        (
            {"TERM_PROGRAM": "vscode", "KITTY_WINDOW_ID": "1"},
            "\x1b]777;notify;Aldwin;",
        ),
        ({"TERM_PROGRAM": "Apple_Terminal"}, ""),
        ({"TERM_PROGRAM": "unknown"}, ""),
        ({}, ""),
    ],
)
def test_protocol_detection(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], prefix: str
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    buf = io.StringIO()
    notify_terminal(buf, "Aldwin finished (45s)", is_terminal=True)
    expected = prefix + "Aldwin finished (45s)\a" if prefix else "\a"
    assert buf.getvalue() == expected


@pytest.mark.parametrize("program", ["iTerm.app", "vscode", "unknown"])
@pytest.mark.parametrize("mode", ["plain", "pipe", "no_color", "empty_no_color"])
def test_quiet_modes(monkeypatch: pytest.MonkeyPatch, program: str, mode: str) -> None:
    monkeypatch.setenv("TERM_PROGRAM", program)
    if mode in {"no_color", "empty_no_color"}:
        monkeypatch.setenv("NO_COLOR", "1" if mode == "no_color" else "")
    buf = io.StringIO()
    notify_terminal(
        buf,
        "Aldwin finished (45s)",
        plain=mode == "plain",
        is_terminal=mode != "pipe",
    )
    notify_terminal(buf, plain=mode == "plain", is_terminal=mode != "pipe")
    assert buf.getvalue() == ""


@pytest.mark.parametrize("program", ["iTerm.app", "vscode"])
def test_notification_payload_cannot_terminate_osc(
    monkeypatch: pytest.MonkeyPatch, program: str
) -> None:
    monkeypatch.setenv("TERM_PROGRAM", program)
    buf = io.StringIO()
    notify_terminal(buf, "name\x07\x1b]0;injected\x9c\r\n", is_terminal=True)
    output = buf.getvalue()
    assert output.count("\x1b") == 1
    assert output.count("\a") == 1
    assert "\r" not in output and "\n" not in output and "\x9c" not in output
    assert "0;injected" not in output


def test_payload_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    buf = io.StringIO()
    notify_terminal(buf, "a" * 1000, is_terminal=True)
    assert buf.getvalue() == "\x1b]9;" + "a" * 160 + "\a"


@pytest.mark.parametrize("failure", ["write", "flush"])
def test_output_errors_do_not_interrupt_chat(failure: str) -> None:
    class BrokenOutput(io.StringIO):
        def write(self, text: str) -> int:
            if failure == "write":
                raise OSError("terminal closed")
            return super().write(text)

        def flush(self) -> None:
            raise OSError("terminal closed")

    notify_terminal(BrokenOutput(), "Aldwin finished (45s)", is_terminal=True)


def _renderer(*, terminal: bool = True) -> tuple[ChatRenderer, io.StringIO]:
    buf = io.StringIO()
    console = create_console(file=buf, width=80, force_terminal=terminal)
    return ChatRenderer(console), buf


@pytest.mark.parametrize("live", [False, True])
def test_approval_notifies_once_without_details(
    monkeypatch: pytest.MonkeyPatch, live: bool
) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    chat, buf = _renderer()
    event = ApprovalEvent(
        re="r", approval_id="ap", tool="run_command", summary=CANARY, detail=CANARY
    )
    turn = TurnRenderer(chat)
    try:
        if live:
            turn.pause_for_approval(event)
            turn.set_approval_selection(1)
            turn.expand_approval_panel(event)
        else:
            chat.render_approval_panel(event)
    finally:
        turn.close()
    output = buf.getvalue()
    assert output.count("\x1b]9;") == 1
    notification = output.split("\x1b]9;", 1)[1].split("\a", 1)[0]
    assert notification == "Aldwin needs your approval: run_command"
    assert CANARY not in notification


def test_approval_without_tool_uses_generic_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    chat, buf = _renderer()
    chat.notify_approval(ApprovalEvent(re="r", approval_id="ap", summary=CANARY))
    assert buf.getvalue() == "\x1b]777;notify;Aldwin;Aldwin needs your approval\a"


@pytest.mark.parametrize("seconds", [0.0, 29.99, 30.0, 45.0])
def test_finished_turn_threshold(
    monkeypatch: pytest.MonkeyPatch, seconds: float
) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    chat, buf = _renderer()
    now = 0.0
    turn = TurnRenderer(chat, clock=lambda: now)
    turn.on_text(CANARY)
    now = seconds
    turn.finish_done(DoneEvent(re="r", reason="answered"))
    output = buf.getvalue()
    if seconds >= 30:
        notification = output.split("\x1b]9;", 1)[1].split("\a", 1)[0]
        assert notification == f"Aldwin finished ({int(seconds)}s)"
        assert CANARY not in notification
    else:
        assert "\x1b]9;" not in output


@pytest.mark.parametrize("reason", ["stopped", "max_steps"])
def test_unfinished_turn_does_not_notify(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    chat, buf = _renderer()
    now = 0.0
    turn = TurnRenderer(chat, clock=lambda: now)
    now = 45.0
    turn.finish_done(DoneEvent(re="r", reason=reason))
    assert "\x1b]9;" not in buf.getvalue()


def test_redirected_renderer_is_silent() -> None:
    chat, buf = _renderer(terminal=False)
    chat.notify("Aldwin finished (45s)")
    chat.ring_bell()
    assert buf.getvalue() == ""


async def test_notify_toggle_help_completion_and_session_scope(
    aldwin_home: Path,
) -> None:
    config = aldwin_home / "config.yaml"
    before = config.read_bytes()
    chat, buf = _renderer()

    async def prompt(_label: str) -> str:
        return ""

    assert chat.notifications_enabled
    for command, enabled in [("/notify", False), ("/notify", True)]:
        should_exit = await handle_slash_command(
            command, client=FakeClient(), renderer=chat, prompt_line=prompt
        )
        assert should_exit is False
        assert chat.notifications_enabled is enabled
        assert f"notify: {'on' if enabled else 'off'}" in buf.getvalue()
        buf.truncate(0)
        buf.seek(0)
        chat.ring_bell()
        assert buf.getvalue() == ("\a" if enabled else "")
    await handle_slash_command(
        "/help", client=FakeClient(), renderer=chat, prompt_line=prompt
    )
    assert "/notify" in buf.getvalue()
    completions = SlashCompleter().get_completions(Document("/not"), CompleteEvent())
    assert [item.text for item in completions] == ["/notify"]
    assert "/notify" in Path("docs/commands.md").read_text(encoding="utf-8")
    assert config.read_bytes() == before
    fresh, _ = _renderer()
    assert fresh.notifications_enabled
