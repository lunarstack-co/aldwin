"""Unit tests for Aldwin CLI theme, markdown block splitting, and Rich rendering."""

from __future__ import annotations

import asyncio
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
from rich.console import Group
from rich.text import Text

from aldwin.cli.render import (
    ChatRenderer,
    clean_tool_summary,
    format_display_args,
    split_markdown_blocks,
)
from aldwin.cli.theme import (
    create_console,
    filter_canonical_models,
    format_duration_ms,
    format_elapsed_s,
    format_relative_time,
    format_tokens,
)
from aldwin.protocol import (
    ApprovalEvent,
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    HistoryEvent,
    HistoryItem,
    TodoItem,
    TodoUpdatedEvent,
    TokenUsage,
    ToolCallEvent,
    ToolResultEvent,
)


def test_theme_formatters() -> None:
    """Formatters produce expected compact strings for tokens, durations, and IDs."""
    assert format_tokens(0) == "0"
    assert format_tokens(450) == "450"
    assert format_tokens(1100) == "1.1k"
    assert format_tokens(6200) == "6.2k"

    assert format_duration_ms(200) == "0.2s"
    assert format_duration_ms(1200) == "1.2s"
    assert format_elapsed_s(24.8) == "24s"

    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    assert format_relative_time("2026-10-18T09:15:00+00:00", now=now) == "today 09:15"
    assert (
        format_relative_time("2026-10-17T18:02:00+00:00", now=now) == "yesterday 18:02"
    )
    assert (
        format_relative_time("2026-10-10T14:30:00+00:00", now=now) == "2026-10-10 14:30"
    )
    assert format_relative_time(None, now=now) == "earlier"
    assert format_relative_time("not-a-timestamp", now=now) == "earlier"


def test_split_markdown_blocks_preserves_code_fences_with_blank_lines() -> None:
    """Blank lines inside ``` or ~~~ fences never split the block."""
    raw = (
        "First paragraph.\n\n"
        "```python\n"
        "def add(a: int, b: int) -> int:\n\n"
        "    return a + b\n"
        "```\n\n"
        "Trailing unfinished"
    )
    completed, remainder = split_markdown_blocks(raw)
    assert len(completed) == 2
    assert completed[0] == "First paragraph."
    assert "def add(a: int, b: int) -> int:\n\n    return a + b" in completed[1]
    assert remainder == "Trailing unfinished"


def test_split_markdown_blocks_holds_unclosed_fence_in_remainder() -> None:
    """An unclosed code fence stays in remainder even when it contains blank lines."""
    raw = "Intro.\n\n~~~bash\necho one\n\necho two\n"
    completed, remainder = split_markdown_blocks(raw)
    assert completed == ["Intro."]
    assert remainder.startswith("~~~bash")
    assert "echo two" in remainder


def test_render_history_quiet_on_open_hides_messages() -> None:
    """Quiet reopen shows only continuing-from hint, not the transcript."""
    buf = io.StringIO()
    console = create_console(
        plain=False,
        width=80,
        record=True,
        file=buf,
        force_terminal=True,
    )
    renderer = ChatRenderer(console, plain=False)

    items = [
        HistoryItem(
            role="user" if i % 2 == 0 else "assistant",
            text=f"message-{i + 6}",
        )
        for i in range(20)
    ]
    event = HistoryEvent(re="req_hist", messages=items, interrupted=False)
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    renderer.render_history(
        event,
        updated_at="2026-10-17T18:02:00+00:00",
        now=now,
        show_messages=False,
    )

    text = console.export_text()
    assert "message-6" not in text
    assert "message-25" not in text
    assert "continuing from yesterday 18:02 · /history to review" in text
    assert "interrupted" not in text


def test_render_history_full_shows_all_messages() -> None:
    """`/history` path prints every message in the event."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)
    items = [
        HistoryItem(role="user", text="hello"),
        HistoryItem(role="assistant", text="hi there"),
    ]
    event = HistoryEvent(re="req_hist", messages=items, interrupted=False)
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    renderer.render_history(
        event,
        updated_at="2026-10-17T18:02:00+00:00",
        now=now,
        show_messages=True,
    )
    text = console.export_text()
    assert "❯ hello" in text
    assert "● hi there" in text
    assert "2 messages · from yesterday 18:02" in text


def test_render_history_interrupted_notice_shown_once() -> None:
    """Interrupted flag shows the interrupted recovery notice once."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    event = HistoryEvent(
        re="req_hist",
        messages=[HistoryItem(role="user", text="check disk")],
        interrupted=True,
    )
    renderer.render_history(event, show_messages=False)

    text = console.export_text()
    assert "check disk" not in text
    assert "Your last request was interrupted." in text
    assert "continue" in text
    assert text.count("Your last request was interrupted.") == 1


def test_turn_renderer_streams_three_markdown_blocks_with_code_fence() -> None:
    """3 markdown blocks incl. code fence print permanently in order once."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    now = 100.0
    turn = renderer.start_turn(clock=lambda: now)
    turn.on_text("First block of text.\n\n")
    turn.on_text("```python\ndef greet():\n\n    return 'hi'\n```\n\n")
    turn.on_text("Third final block.")
    now = 104.0
    tokens = turn.finish_done(
        DoneEvent(
            re="r1",
            reason="answered",
            usage=TokenUsage(input_tokens=800, output_tokens=300),
        )
    )

    assert tokens == 1100
    text = console.export_text()
    assert text.count("First block of text.") == 1
    assert text.count("def greet():") == 1
    assert text.count("Third final block.") == 1
    assert (
        text.index("First block of text.")
        < text.index("def greet():")
        < text.index("Third final block.")
    )
    assert "Worked for 4s · 1.1k tokens" in text


def test_turn_renderer_tool_call_and_result_cards() -> None:
    """tool_call and tool_result render ●, ✓, and ✗ cards with human labels."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    now = 10.0
    turn = renderer.start_turn(clock=lambda: now)
    turn.on_tool_call(
        ToolCallEvent(
            re="r1",
            call_id="c1",
            name="web_search",
            args_preview='"Python 3.14"',
        )
    )
    assert turn._spinner_text == "Running Search…"

    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=True,
            summary="5 results",
            duration_ms=1200,
        )
    )
    turn.on_tool_call(
        ToolCallEvent(
            re="r1",
            call_id="c2",
            name="run_command",
            args_preview="sudo rm -rf /",
        )
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c2",
            ok=False,
            summary="sudo is not allowed",
            duration_ms=5,
        )
    )
    now = 15.0
    turn.finish_done(DoneEvent(re="r1", reason="answered"))

    text = console.export_text()
    assert "✓ Search  1.2s · 5 results" in text
    assert "✗ shell" in text
    assert "sudo is not allowed" in text
    assert "Worked for 5s · 2 tools" in text


def test_render_error_codes_map_to_house_copy() -> None:
    """Every protocol ErrorCode renders its exact one-line copy and next step."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    codes: list[ErrorCode] = [
        "auth_failed",
        "rate_limited",
        "model_unavailable",
        "unknown_model",
        "busy",
        "internal",
    ]
    for code in codes:
        renderer.render_error(code, "raw message")

    text = console.export_text()
    assert "Your OpenAI key was rejected. Run `aldwin setup`." in text
    assert "OpenAI is rate-limiting requests right now." in text
    assert (
        "Can't reach OpenAI. Check your connection, then press ↑ Enter to retry."
        in text
    )
    assert "Pick another with /model." in text
    assert "Press Esc to stop it first." in text
    assert "Something went wrong. Details are in ~/.aldwin/logs/aldwin.log" in text
    assert "Traceback" not in text


def test_render_error_prefers_host_busy_copy_for_rate_limited() -> None:
    """503-style host messages keep 'busy' wording instead of rate-limit house copy."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)
    renderer.render_error(
        "rate_limited",
        "OpenAI is busy right now. Wait a moment and try again.",
    )
    text = console.export_text()
    assert "OpenAI is busy right now." in text
    assert "rate-limiting" not in text


def test_plain_mode_has_no_ansi_spinner_live_or_bell() -> None:
    """Plain mode emits zero ANSI escape codes, spinner frames, or bell chars."""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    renderer.render_welcome(first_run=False, model="gpt-4o", display_home="~/.aldwin")
    renderer.render_history(
        HistoryEvent(
            re="h1",
            messages=[HistoryItem(role="user", text="hello")],
            interrupted=True,
        )
    )
    turn = renderer.start_turn()
    turn.on_text("Paragraph one.\n\nParagraph two.")
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="run_command", args_preview="df -h")
    )
    turn.pause_for_approval(
        ApprovalEvent(
            re="r1",
            approval_id="ap_1",
            summary="Run this command?",
            detail="df -h",
        )
    )
    turn.resume_after_approval()
    turn.on_tool_result(
        ToolResultEvent(
            re="r1", call_id="c1", ok=True, summary="exit 0", duration_ms=200
        )
    )
    turn.finish_done(DoneEvent(re="r1", reason="answered"))

    raw = buf.getvalue()
    assert "\x1b[" not in raw
    assert "\a" not in raw
    for frame in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏":
        assert frame not in raw
    assert "Paragraph one." in raw
    assert "Paragraph two." in raw
    assert "Run this command?" in raw
    assert "✓ shell  exit 0" in raw


def test_turn_renderer_status_ticker_retry_notice_and_max_steps() -> None:
    """Status line formats elapsed & tools; retry notice and max_steps render."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    now = 0.0
    turn = renderer.start_turn(clock=lambda: now)
    for idx in range(3):
        cid = f"c{idx}"
        turn.on_tool_call(ToolCallEvent(re="r1", call_id=cid, name="web_search"))
        turn.on_tool_result(
            ToolResultEvent(
                re="r1", call_id=cid, ok=True, summary="ok", duration_ms=100
            )
        )
    now = 12.4
    assert turn.format_status_line() == "(12s · ↓ … tokens · esc to interrupt)"

    turn.on_status("OpenAI is busy. Retrying in 2s (2/3)…")
    assert turn._retry_status == "OpenAI is busy. Retrying in 2s (2/3)…"
    turn.finish_done(DoneEvent(re="r1", reason="max_steps", max_steps=42))

    text = console.export_text()
    # In-place Live status is not stacked as permanent scrollback lines.
    assert text.count("OpenAI is busy. Retrying") == 0
    assert "Stopped after 42 steps. Say continue to keep going." in text


@pytest.mark.parametrize(
    ("max_steps", "expected"),
    [
        (1, "Stopped after 1 step. Say continue to keep going."),
        (None, "Stopped at the step limit. Say continue to keep going."),
    ],
)
@pytest.mark.parametrize("plain", [False, True])
def test_turn_renderer_max_steps_notice_uses_reported_limit(
    max_steps: int | None, expected: str, plain: bool
) -> None:
    """The max-steps notice reflects the limit carried on `DoneEvent`."""
    buf = io.StringIO()
    console = create_console(plain=plain, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=plain)

    turn = renderer.start_turn(clock=lambda: 0.0)
    turn.finish_done(DoneEvent(re="r1", reason="max_steps", max_steps=max_steps))

    assert expected in console.export_text().replace("*", "")


def test_turn_renderer_flushes_narration_before_tool_card() -> None:
    """Buffered text before a tool call prints above the tool card."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    turn = renderer.start_turn()
    turn.on_text("Let me check your disk usage.")
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="run_command", args_preview="df -h")
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1", call_id="c1", ok=True, summary="exit 0", duration_ms=100
        )
    )
    turn.on_text("You have 41 GB free.")
    turn.finish_done(DoneEvent(re="r1", reason="answered"))

    text = console.export_text()
    idx_narration = text.index("Let me check your disk usage.")
    idx_tool = text.index("✓ shell")
    idx_answer = text.index("You have 41 GB free.")
    assert idx_narration < idx_tool < idx_answer


def test_narrow_60_column_rendering() -> None:
    """Panels and tool cards wrap cleanly within 60 columns."""
    buf = io.StringIO()
    console = create_console(width=60, record=True, file=buf)
    renderer = ChatRenderer(console)

    renderer.render_welcome(first_run=False, model="gpt-4o", display_home="~/.aldwin")
    renderer.render_approval_panel(
        ApprovalEvent(
            re="r1",
            approval_id="ap_1",
            summary="Run this command?",
            detail="df -h /System/Volumes/Data/Users/fola/Documents",
        )
    )
    turn = renderer.start_turn()
    turn.on_tool_call(
        ToolCallEvent(
            re="r1",
            call_id="c1",
            name="web_fetch",
            args_preview="https://docs.python.org/3/whatsnew/3.14.html",
        )
    )
    turn.finish_error(ErrorEvent(re="r1", code="model_unavailable", message="offline"))

    text = console.export_text()
    for line in text.splitlines():
        assert len(line) <= 60, f"Line exceeded 60 columns ({len(line)}): {line!r}"


def test_multiline_approval_panel_and_turn_close_cleanup() -> None:
    """Multi-line approval detail places [N] no on its own line; close stops Live."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    renderer.render_approval_panel(
        ApprovalEvent(
            re="r1",
            approval_id="ap_persona",
            summary="Update persona?",
            detail="Line one of new persona.\nLine two of new persona.",
        )
    )
    text = console.export_text()
    assert "Line one of new persona." in text
    assert "Line two of new persona." in text
    assert "Do you want to proceed?" in text
    assert "No" in text

    turn = renderer.start_turn()
    assert turn._live is not None
    turn.close()
    assert turn._live is None


def test_format_display_args_and_clean_tool_summary() -> None:
    """JSON tool args unwrap to clean strings; load_skill header becomes 'loaded'."""
    assert (
        format_display_args('{"query": "Folarin Akinloye Cuvama"}')
        == '"Folarin Akinloye Cuvama"'
    )
    assert format_display_args('{"command": "df -h"}') == "df -h"
    assert (
        format_display_args('{"url": "https://agentling.dev"}')
        == "https://agentling.dev"
    )
    assert format_display_args('{"name": "using-aldwin"}') == "using-aldwin"
    assert (
        format_display_args('{"text": "The user\'s name is Folarin."}')
        == '"The user\'s name is Folarin."'
    )
    assert (
        format_display_args(
            '{"new_text": "Line 1\\nLine 2\\nLine 3"}', tool_name="set_persona"
        )
        == "persona.md · 3 lines"
    )
    assert clean_tool_summary("load_skill", "# Using Aldwin") == "loaded"
    assert clean_tool_summary("web_search", "5 results") == "5 results"


def test_wide_120_column_panels_cap_at_76_columns() -> None:
    """Panels cap width at <= 76 columns on wide 120-column terminals."""
    buf = io.StringIO()
    console = create_console(width=120, record=True, file=buf)
    renderer = ChatRenderer(console)
    renderer.render_welcome(first_run=True, model="gpt-4o", display_home="~/.aldwin")

    for line in console.export_text().splitlines():
        assert len(line.rstrip()) <= 76


def test_filter_canonical_models() -> None:
    """filter_canonical_models hides dated snapshots and puts current model first."""
    raw = [
        "gpt-5-2025-08-07",
        "gpt-5",
        "gpt-5-chat-latest",
        "gpt-5.2-codex",
        "gpt-4.1",
        "gpt-4.1-2025-04-14",
        "gpt-4o",
    ]
    filtered, hidden = filter_canonical_models(raw, "gpt-4o")
    assert filtered == ["gpt-4o", "gpt-5", "gpt-4.1"]
    assert hidden == 4


def test_resolve_approval_collapses_to_one_line_and_list_paragraph_spacing() -> None:
    """resolve_approval prints one-line record; markdown blocks have blank line."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)
    turn = renderer.start_turn()

    ev = ApprovalEvent(
        re="r1",
        approval_id="ap_1",
        summary="Run this command?",
        detail="df -h",
    )
    turn.pause_for_approval(ev)
    turn.resolve_approval(ev, "yes")
    turn.on_text("- item one\n- item two\n\nThis overview condenses the result.")
    turn.finish_done(DoneEvent(re="r1", reason="answered"))

    text = console.export_text()
    assert "✓ approved  df -h" in text
    assert "\n\nThis overview condenses the result." in text


def test_format_display_args_unwraps_truncated_json_fallback() -> None:
    """`format_display_args` unwraps truncated JSON strings without raw `{`."""
    assert (
        format_display_args(
            '{"command": "echo alpha_beta_gamma_delta…',
            tool_name="run_command",
        )
        == "echo alpha_beta_gamma_delta…"
    )
    assert (
        format_display_args(
            '{"query": "long search query truncated…',
            tool_name="web_search",
        )
        == '"long search query truncated…"'
    )


def test_tool_result_80_cols_preserves_duration_and_summary() -> None:
    """80-col tool result row caps `display_args` and keeps `duration`."""
    buf = io.StringIO()
    console = create_console(
        width=80,
        record=True,
        file=buf,
        force_terminal=True,
    )
    renderer = ChatRenderer(console)
    turn = renderer.start_turn()
    try:
        turn.on_tool_call(
            ToolCallEvent(
                re="r1",
                call_id="c1",
                name="run_command",
                args_preview="echo " + ("x" * 110),
            )
        )
        turn.on_tool_result(
            ToolResultEvent(
                re="r1",
                call_id="c1",
                ok=True,
                summary="exit 0",
                duration_ms=120,
            )
        )
        turn.on_tool_call(
            ToolCallEvent(
                re="r1",
                call_id="c2",
                name="run_command",
                args_preview="echo " + ("y" * 110),
            )
        )
        turn.on_tool_result(
            ToolResultEvent(
                re="r1",
                call_id="c2",
                ok=False,
                summary="exit 1",
                duration_ms=50,
            )
        )
    finally:
        turn.close()

    exported = console.export_text().splitlines()
    ok_lines = [
        line[line.index("  ✓ shell") :].rstrip()
        for line in exported
        if "  ✓ shell" in line
    ]
    assert len(ok_lines) == 1
    assert len(ok_lines[0]) <= 80
    assert ok_lines[0].startswith("  ✓ shell  echo xxx")
    # Sub-1s durations are omitted from the permanent row (FR-8).
    assert ok_lines[0].endswith("exit 0")

    err_lines = [
        line[line.index("  ✗ shell") :].rstrip()
        for line in exported
        if "  ✗ shell" in line
    ]
    assert len(err_lines) == 1
    assert len(err_lines[0]) <= 80
    assert err_lines[0].startswith("  ✗ shell  echo yyy")
    # FR-8: failure reason is on the following ⎿ line, not the glyph row.
    assert any("exit 1" in line for line in exported)


def test_approval_panel_strips_ansi_bidi_and_redacts_secrets() -> None:
    """Panel `summary` and `detail` strip ANSI/bidi and redact secrets."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    ev = ApprovalEvent(
        re="r1",
        approval_id="ap_san",
        tool="run_command",
        tier="ask",
        summary="Edit \x1b[31mnotes\u202e.md",
        detail="OPENAI_API_KEY=sk-1234567890abcdefghijklmno",
        risky=["pipes into a shell", "network download"],
    )
    renderer.render_approval_panel(ev)
    text = console.export_text()
    assert "Edit notes.md" in text
    assert "\u202e" not in text
    assert "sk-1234567890" not in text
    assert "OPENAI_API_KEY=‹redacted›" in text
    assert "⚠ pipes into a shell · network download" in text


def test_diff_approval_panel_folds_at_24_lines_and_expands() -> None:
    """Diff approval panel folds after 24 lines and expands when requested."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    diff_lines = ["--- a/notes.md", "+++ b/notes.md", "@@ -1,28 +1,28 @@"] + [
        f"+added line {i}" if i % 2 == 0 else f"-removed line {i}" for i in range(1, 28)
    ]
    assert len(diff_lines) == 30
    ev = ApprovalEvent(
        re="r1",
        approval_id="ap_diff",
        tool="edit_file",
        tier="diff",
        summary="Edit notes.md",
        detail="\n".join(diff_lines),
        grantable=True,
        grant_scope="file",
    )

    renderer.render_approval_panel(ev, expanded=False)
    folded_text = console.export_text()
    assert "… 6 more lines (ctrl+o to expand)" in folded_text
    assert "+added line 26" not in folded_text
    assert "Do you want to proceed?" in folded_text
    assert "Yes, for this session" in folded_text
    assert "No" in folded_text

    buf_exp = io.StringIO()
    console_exp = create_console(width=80, record=True, file=buf_exp)
    renderer_exp = ChatRenderer(console_exp)
    renderer_exp.render_approval_panel(ev, expanded=True)
    expanded_text = console_exp.export_text()
    assert "+added line 26" in expanded_text
    assert "more lines (ctrl+o to expand)" not in expanded_text


def test_diff_approval_clips_long_lines_until_expanded() -> None:
    """Folded Diff panels clip long lines; expand rebuilds without the notice."""
    marker = "UNIQUE_LONG_MARKER_ABCDEF"
    long_plus = "+" + ("x" * 200) + marker
    ev = ApprovalEvent(
        re="r1",
        approval_id="ap_long",
        tool="edit_file",
        tier="diff",
        summary="Edit a.py",
        detail=f"--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n{long_plus}",
        grantable=True,
        grant_scope="file",
    )

    folded_buf = io.StringIO()
    folded_console = create_console(
        width=60, record=True, file=folded_buf, force_terminal=True
    )
    ChatRenderer(folded_console).render_approval_panel(ev, expanded=False)
    folded = folded_console.export_text()
    assert "long lines truncated (ctrl+o to expand)" in folded
    assert marker not in folded

    # Inspect the expanded panel renderable directly. export_text can
    # ellipsize tall panels in some CI terminals.
    exp_console = create_console(width=60, force_terminal=True)
    panel = ChatRenderer(exp_console).build_approval_panel(ev, expanded=True)
    assert isinstance(panel.renderable, Group)
    joined = "\n".join(
        part.plain if isinstance(part, Text) else str(part)
        for part in panel.renderable.renderables
    )
    assert "long lines truncated" not in joined
    assert marker in joined.replace("\n", "")


def test_resolve_diff_approval_uses_summary_not_persona() -> None:
    """Declined Diff rows echo the edit summary, not a persona.md line count."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    turn = ChatRenderer(console).start_turn()
    ev = ApprovalEvent(
        re="r1",
        approval_id="ap_d",
        tool="edit_file",
        tier="diff",
        summary="Edit notes.md (+1 −1)",
        detail="--- a/notes.md\n+++ b/notes.md\n@@ -1 +1 @@\n-old\n+new",
    )
    turn.pause_for_approval(ev)
    turn.resolve_approval(ev, "no")
    text = console.export_text()
    assert "✗ declined  Edit notes.md (+1 −1)" in text
    assert "persona.md" not in text


def test_tc44_verbose_help_readme(aldwin_home: Path) -> None:
    """`/verbose` shows preview lines, and help and the commands doc list it."""
    config = aldwin_home / "config.yaml"
    before = config.read_bytes() if config.is_file() else b""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    assert renderer.verbose is False
    preview = "\n".join(f"{number}\trow" for number in range(1, 16))
    turn = renderer.start_turn()
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="read_file", summary="Read a.txt")
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=True,
            summary="Read a.txt · lines 1–15 of 15",
            preview=preview,
        )
    )
    quiet = console.export_text()
    assert "Read a.txt · lines 1–15 of 15" in quiet
    assert "1\trow" not in quiet

    renderer.set_verbose(True)
    turn_on = renderer.start_turn()
    turn_on.on_tool_call(
        ToolCallEvent(re="r1", call_id="c2", name="read_file", summary="Read a.txt")
    )
    turn_on.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c2",
            ok=True,
            summary="Read a.txt · lines 1–15 of 15",
            preview=preview,
        )
    )
    shown = console.export_text()
    preview_rows = [
        line
        for line in shown.splitlines()
        if line.startswith("    ") and line.strip().endswith("row")
    ]
    assert [line.strip().split()[0] for line in preview_rows] == [
        str(number) for number in range(1, 13)
    ]
    assert "13" not in {line.strip().split()[0] for line in preview_rows}
    assert "verbose: on" in shown
    renderer.set_verbose(False)
    assert "verbose: off" in console.export_text()
    after = config.read_bytes() if config.is_file() else b""
    assert after == before

    help_buf = io.StringIO()
    help_console = create_console(plain=True, width=80, record=True, file=help_buf)
    help_renderer = ChatRenderer(help_console, plain=True)

    async def _run_help() -> None:
        from fakes.host import FakeClient

        from aldwin.cli.commands import handle_slash_command

        await handle_slash_command(
            "/help",
            client=FakeClient(),
            renderer=help_renderer,
            prompt_line=_noop_prompt,
        )

    asyncio.run(_run_help())
    help_text = help_console.export_text()
    assert "/verbose" in help_text
    assert "/workspace" in help_text
    commands_doc = Path("docs/commands.md").read_text(encoding="utf-8")
    slash = commands_doc.split("## Slash commands", 1)[1].split("## Keyboard", 1)[0]
    assert "/verbose" in slash
    assert "/workspace" in slash


async def _noop_prompt(_label: str) -> str:
    return ""


def test_tc45_pending_glyph_and_plain() -> None:
    """All pending tools use a bullet; plain mode still shows human labels."""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    turn = renderer.start_turn()
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="read_file", summary="Read a.txt")
    )
    turn.on_tool_call(ToolCallEvent(re="r1", call_id="c2", name="web_search"))
    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=False,
            summary="Read .env · blocked: may contain secrets",
        )
    )
    text = console.export_text()
    assert "● Read a.txt" in text
    assert "● Search" in text
    assert "⚙" not in text
    assert "Read .env · blocked: may contain secrets" in text

    color_buf = io.StringIO()
    color = create_console(
        width=80,
        record=True,
        file=color_buf,
        force_terminal=True,
    )
    color_turn = ChatRenderer(color).start_turn()
    color_turn.on_tool_call(ToolCallEvent(re="r1", call_id="c1", name="read_file"))
    color_turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=False,
            summary="Read .env · blocked: may contain secrets",
        )
    )
    failure = [segment for segment in color._record_buffer if "✗" in segment.text]
    assert failure
    style = failure[0].style
    assert style is not None
    assert "err" in str(style) or style.color is not None


def test_tool_rows_strip_terminal_controls() -> None:
    """File-tool rows and previews drop ANSI and other terminal controls."""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    renderer.set_verbose(True)
    console.export_text()
    turn = renderer.start_turn()
    turn.on_tool_call(
        ToolCallEvent(
            re="r1",
            call_id="c1",
            name="read_file",
            summary="Read \x1b[2Ja.txt",
        )
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=True,
            summary="Read \x1b[2Ja.txt · lines 1–1 of 1",
            preview="1\t\x1b[31mrow",
        )
    )
    shown = console.export_text()
    assert "\x1b" not in shown
    assert "Read a.txt" in shown
    assert "row" in shown


def test_todo_tool_skips_collapsed_row_and_freezes_live_on_done() -> None:
    """`todo` has no ⚙/✓ tool row; turn end freezes any checklist Live."""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    turn = renderer.start_turn(clock=lambda: 0.0)

    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="todo", args_preview="…"),
    )
    renderer.on_todo_updated(
        TodoUpdatedEvent(
            re="r1",
            items=[
                TodoItem(text="List downloads", status="in_progress"),
                TodoItem(text="Rename invoices", status="pending"),
            ],
            done=0,
            total=2,
        )
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=True,
            summary="Tasks 0/2",
            duration_ms=3,
        )
    )
    turn.finish_done(DoneEvent(re="r1", reason="answered"))

    text = console.export_text()
    assert "⚙ todo" not in text
    assert "✓ todo" not in text
    assert "Tasks 0/2" in text
    assert "List downloads" in text
    assert renderer.todo_view.live_active is False
    assert renderer.todo_view.toolbar_label() == "Tasks 0/2"
