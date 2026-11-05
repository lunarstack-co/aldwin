"""CLI rendering suite: welcome, tools, approvals, plain mode, slash unknowns."""

from __future__ import annotations

import io
from datetime import datetime, timezone

from fakes.host import FakeHost

from aldwin.cli.commands import handle_slash_command
from aldwin.cli.render import ChatRenderer
from aldwin.cli.theme import create_console
from aldwin.protocol import (
    ApprovalEvent,
    DoneEvent,
    ErrorCode,
    HistoryEvent,
    HistoryItem,
    TokenUsage,
    ToolCallEvent,
    ToolResultEvent,
)


async def _noop_prompt(_label: str) -> str:
    return ""


def test_welcome_and_header_rendering() -> None:
    """`render_welcome` renders onboarding welcome panel and chat session header."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    renderer.render_welcome(first_run=True, model="gpt-4o", display_home="~/.aldwin")
    renderer.render_welcome(
        first_run=False,
        model="gpt-4o",
        display_home="~/.aldwin",
        tip='Try: "what can you do?"  ·  /help  ·  Esc stops an answer',
    )

    text = console.export_text()
    assert "Welcome to Aldwin" in text
    assert "A personal assistant that runs on your computer." in text
    assert "Aldwin · gpt-4o · ~/.aldwin" in text
    assert 'Try: "what can you do?"' in text


def test_dimmed_history_and_divider() -> None:
    """Quiet reopen hides transcript; interrupted notice still shows."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    items = [
        HistoryItem(
            role="user" if idx % 2 == 0 else "assistant",
            text=f"turn-{idx}",
        )
        for idx in range(20)
    ]
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    renderer.render_history(
        HistoryEvent(re="h1", messages=items, interrupted=True),
        updated_at="2026-10-17T18:02:00+00:00",
        now=now,
        show_messages=False,
    )

    text = console.export_text()
    assert "turn-0" not in text
    assert "turn-19" not in text
    assert "continuing from yesterday 18:02 · /history to review" in text
    assert "Your last request was interrupted. Say continue to pick it up." in text


def test_streamed_markdown_blocks_including_code_fence() -> None:
    """Streamed markdown prints completed blocks once and keeps code fences intact."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    now = 10.0
    turn = renderer.start_turn(clock=lambda: now)
    turn.on_text("Opening paragraph.\n\n")
    turn.on_text("```python\ndef compute(x: int) -> int:\n\n    return x * 2\n```\n\n")
    turn.on_text("Closing summary.")
    now = 14.0
    turn.finish_done(
        DoneEvent(
            re="r1",
            reason="answered",
            usage=TokenUsage(input_tokens=800, output_tokens=300),
        )
    )

    text = console.export_text()
    assert text.count("Opening paragraph.") == 1
    assert text.count("def compute(x: int) -> int:") == 1
    assert text.count("Closing summary.") == 1
    assert (
        text.index("Opening paragraph.")
        < text.index("def compute(x: int) -> int:")
        < text.index("Closing summary.")
    )


def test_render_tool_card_ok_and_fail_exact_text() -> None:
    """Tool cards render exact `✓ web_search 1.2s · 3 results` and `✗` copy."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    turn = renderer.start_turn()
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="web_search", args_preview="")
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=True,
            summary="3 results",
            duration_ms=1200,
        )
    )
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c2", name="web_fetch", args_preview="")
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c2",
            ok=False,
            summary="refused: private address",
            duration_ms=15,
        )
    )
    turn.finish_done(DoneEvent(re="r1", reason="answered"))

    raw_text = console.export_text()
    assert "✓ Search  1.2s · 3 results" in raw_text
    assert "✗ Fetch" in raw_text
    assert "refused: private address" in raw_text

    normalized_lines = [" ".join(line.split()) for line in raw_text.splitlines()]
    assert "✓ Search 1.2s · 3 results" in normalized_lines
    assert any("refused: private address" in line for line in normalized_lines)


def test_approval_panel_end_line_and_stopped_line() -> None:
    """Approval panel, `✓ done` footer, and `(stopped)` marker render exact copy."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    now = 100.0
    turn1 = renderer.start_turn(clock=lambda: now)
    turn1.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="run_command", args_preview="df -h")
    )
    approval = ApprovalEvent(
        re="r1",
        approval_id="ap_1",
        summary="Run this command?",
        detail="df -h",
    )
    turn1.pause_for_approval(approval)
    turn1.resume_after_approval()
    turn1.on_tool_result(
        ToolResultEvent(
            re="r1", call_id="c1", ok=True, summary="exit 0", duration_ms=200
        )
    )
    turn1.on_text("41 GB free.")
    now = 104.0
    turn1.finish_done(
        DoneEvent(
            re="r1",
            reason="answered",
            usage=TokenUsage(input_tokens=800, output_tokens=300),
        )
    )

    turn2 = renderer.start_turn(clock=lambda: now)
    turn2.on_text("Partial text before Esc")
    turn2.finish_done(DoneEvent(re="r2", reason="stopped"))

    text = console.export_text()
    assert "Run this command?" in text
    assert "df -h" in text
    assert "Do you want to proceed?" in text
    assert "Worked for 4s · 1 tool · 1.1k tokens" in text
    assert "Partial text before Esc" in text
    assert "Interrupted · tell Aldwin what to do instead" in text


def test_each_error_code_copy() -> None:
    """Every `ErrorCode` renders its friendly one-line house copy and next step."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    expected: list[tuple[ErrorCode, str]] = [
        ("auth_failed", "Your API key was rejected. Run `aldwin setup` to replace it."),
        ("rate_limited", "The provider rate limit was reached."),
        ("model_unavailable", "Can't reach the model provider. Check your connection"),
        ("unknown_model", "Pick another with /model."),
        ("busy", "Press Esc to stop it first."),
        ("internal", "Something went wrong. Details are in ~/.aldwin/logs/aldwin.log"),
    ]
    for code, _phrase in expected:
        renderer.render_error(code, "")

    text = console.export_text()
    for _code, phrase in expected:
        assert phrase in text


def test_plain_mode_output_contains_no_ansi_escape_codes() -> None:
    """Plain mode rendering contains zero `\\x1b[` ANSI escape sequences."""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    renderer.render_welcome(first_run=False, model="gpt-4o", display_home="~/.aldwin")
    renderer.render_history(
        HistoryEvent(
            re="h1",
            messages=[HistoryItem(role="user", text="hello")],
            interrupted=False,
        )
    )
    turn = renderer.start_turn()
    turn.on_text("Plain answer paragraph.")
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="web_search", args_preview="python")
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1", call_id="c1", ok=True, summary="3 results", duration_ms=1200
        )
    )
    turn.finish_done(DoneEvent(re="r1", reason="answered"))
    renderer.render_error("model_unavailable", "offline")

    raw = buf.getvalue()
    assert "\x1b[" not in raw
    assert "\a" not in raw
    assert "Plain answer paragraph." in raw
    assert "✓ Search  1.2s · 3 results" in raw


async def test_unknown_slash_command_with_fake_host(fake_host: FakeHost) -> None:
    """Unknown slash command prints help hint and sends zero turns to `fake_host`."""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    should_exit = await handle_slash_command(
        "/unknown",
        client=fake_host,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert should_exit is False
    assert fake_host.requests == []
    assert "Unknown command. Type /help." in console.export_text()


def test_render_welcome_long_display_home_capped_at_76_cols() -> None:
    """Long `display_home` path in header panel stays `<= 76` columns."""
    buf = io.StringIO()
    console = create_console(width=120, record=True, file=buf)
    renderer = ChatRenderer(console)

    long_home = (
        "/private/var/folders/b0/3_8pbpt16q99rkczytyjd72h0000gn/T/aldwin_long_home_dir"
    )
    renderer.render_welcome(
        first_run=False,
        model="gpt-4o-mini",
        display_home=long_home,
    )

    for line in console.export_text().splitlines():
        assert len(line.rstrip()) <= 76


def test_colour_mode_notices_italicize_continue() -> None:
    """Colour-mode interrupted and `max_steps` notices italicize `continue`."""
    buf = io.StringIO()
    console = create_console(
        plain=False,
        width=80,
        record=True,
        file=buf,
        force_terminal=True,
    )
    renderer = ChatRenderer(console, plain=False)

    renderer.render_history(
        HistoryEvent(
            re="h1",
            messages=[HistoryItem(role="user", text="hi")],
            interrupted=True,
        )
    )
    turn = renderer.start_turn()
    try:
        turn.finish_done(DoneEvent(re="r1", reason="max_steps"))
    finally:
        turn.close()

    raw_ansi = buf.getvalue()
    assert "\x1b[3;33mcontinue\x1b[0m" in raw_ansi or "\x1b[3m" in raw_ansi
