"""Integration and async key-listener tests for Aldwin's chat screen."""

from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fakes.host import FakeClient
from prompt_toolkit.formatted_text import to_plain_text
from prompt_toolkit.input import Input
from prompt_toolkit.input.defaults import create_pipe_input
from rich.console import Console

from aldwin.cli.chat_screen import (
    WHATS_NEW_TEXT,
    TurnKeyListener,
    build_prompt_session,
    format_bottom_toolbar,
    format_toolbar_model_segment,
    read_approval_key,
    render_whats_new_if_due,
    run_chat,
)
from aldwin.cli.render import CONTEXT_NEARLY_FULL_NOTICE, ChatRenderer
from aldwin.cli.theme import PLAIN_PROMPT_STYLE, create_console
from aldwin.client import BootstrapResult, bootstrap, mark_whats_new_seen, whats_new_due
from aldwin.core.settings import load_config
from aldwin.protocol import (
    ApprovalEvent,
    DoneEvent,
    TextEvent,
    TokenUsage,
    ToolCallEvent,
    ToolResultEvent,
)

_CHAT_TIMEOUT_S = 10


async def _run_chat(
    boot: BootstrapResult,
    *,
    client: FakeClient,
    console: Console,
    input_dev: Input,
    key_status: str = "ok",
) -> int:
    """Run one chat session, and fail the test if it never returns.

    A missed pipe wakeup used to block ``prompt_async`` until the CI job was
    killed, which looks like a stuck pytest step.
    """
    with patch(
        "aldwin.client.verify_active_openai_key",
        new=AsyncMock(return_value=key_status),
    ):
        return await asyncio.wait_for(
            run_chat(boot, client=client, console=console, input_dev=input_dev),
            timeout=_CHAT_TIMEOUT_S,
        )


async def test_chat_approval_yes_sends_approve_and_resumes(aldwin_home: Path) -> None:
    """approval event + y shows panel, rings bell, approves, and resumes."""
    _ = aldwin_home
    boot = bootstrap(plain=False)
    client = FakeClient()
    client.queue_turn(
        [
            ToolCallEvent(
                re="r1",
                call_id="c1",
                name="run_command",
                args_preview="df -h",
            ),
            ApprovalEvent(
                re="r1",
                approval_id="ap_1",
                summary="Run this command?",
                detail="df -h",
            ),
            ToolResultEvent(
                re="r1",
                call_id="c1",
                ok=True,
                summary="exit 0",
                duration_ms=200,
            ),
            TextEvent(re="r1", delta="You have 41 GB free."),
            DoneEvent(
                re="r1",
                reason="answered",
                usage=TokenUsage(input_tokens=800, output_tokens=300),
            ),
        ]
    )

    buf = io.StringIO()
    console = create_console(plain=False, width=80, record=True, file=buf)

    with create_pipe_input() as pipe_in:
        pipe_in.send_text("is my disk nearly full?\r")

        async def _send_approval_and_exit() -> None:
            await client.approval_emitted_event.wait()
            pipe_in.send_text("y/exit\r")

        feeder = asyncio.create_task(_send_approval_and_exit())
        rc = await _run_chat(
            boot,
            client=client,
            console=console,
            input_dev=pipe_in,
        )
        await feeder

    assert rc == 0
    assert client.approvals == [("ap_1", "yes")]
    assert "\a" in buf.getvalue()
    text = console.export_text()
    assert "Run this command?" in text
    assert "df -h" in text
    assert "Do you want to proceed?" in text
    assert "No" in text
    assert "You have 41 GB free." in text
    assert "Saved. See you." in text


async def test_chat_approval_escape_and_enter_send_no() -> None:
    """read_approval_key resolves Esc, n, and Enter as 'no'."""
    with create_pipe_input() as pipe_esc:
        pipe_esc.send_text("\x1b")
        ans_esc = await asyncio.wait_for(read_approval_key(pipe_esc), timeout=0.5)
        assert ans_esc == "no"

    with create_pipe_input() as pipe_n:
        pipe_n.send_text("n")
        ans_n = await asyncio.wait_for(read_approval_key(pipe_n), timeout=0.5)
        assert ans_n == "no"

    with create_pipe_input() as pipe_enter:
        pipe_enter.send_text("\r")
        ans_enter = await asyncio.wait_for(read_approval_key(pipe_enter), timeout=0.5)
        assert ans_enter == "no"


async def test_escape_during_stream_stops_under_one_second(aldwin_home: Path) -> None:
    """Esc during stream sends stop in < 1 s and prints (stopped)."""
    _ = aldwin_home
    boot = bootstrap(plain=True)
    client = FakeClient()
    client.hang_until_stopped = True
    client.queue_turn([TextEvent(re="r1", delta="Partial answer before stop")])

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)

    with create_pipe_input() as pipe_in:
        pipe_in.send_text("tell me a long story\r")
        elapsed = 0.0

        async def _press_esc_then_exit() -> None:
            nonlocal elapsed
            await client.turn_started_event.wait()
            await asyncio.sleep(0.02)
            t0 = time.perf_counter()
            pipe_in.send_text("\x1b")
            await client._stop_gate.wait()
            elapsed = time.perf_counter() - t0
            await asyncio.sleep(0.05)
            pipe_in.send_text("/exit\r")

        feeder = asyncio.create_task(_press_esc_then_exit())
        rc = await _run_chat(
            boot,
            client=client,
            console=console,
            input_dev=pipe_in,
        )
        await feeder

    assert rc == 0
    assert client.stop_calls == 1
    assert elapsed < 1.0, f"Esc stop took {elapsed:.3f}s (budget < 1.0s)"
    text = console.export_text()
    assert "Partial answer before stop" in text
    assert "Interrupted · tell Aldwin what to do instead" in text


async def test_prompt_input_bindings_empty_ignored_and_toolbar(
    aldwin_home: Path,
) -> None:
    """Alt+Enter inserts newline, empty input ignored, toolbar formatted."""
    _ = aldwin_home
    assert (
        format_bottom_toolbar("gpt-4o", "2026-10-18_3f2a9b1c", 6200)
        == " ? for shortcuts   gpt-4o · ██████████95% context left "
    )
    assert (
        format_bottom_toolbar(
            "gpt-4o",
            "2026-10-18_3f2a9b1c",
            6200,
            todo_label="Tasks 2/5",
            plain=True,
        )
        == " ? for shortcuts   gpt-4o · ##########95% context left · Tasks 2/5 "
    )
    # gpt-4o window 128k → 15% left needs ~108_800 used; 8% needs ~117_760.
    warn = to_plain_text(format_toolbar_model_segment("gpt-4o", 108_800, plain=True))
    assert "15% context left" in warn
    assert "/new to start fresh" in warn
    assert "warn" in warn
    warn_ft = format_toolbar_model_segment("gpt-4o", 108_800, plain=True)
    assert any(
        frag[0] == "class:bottom-toolbar.warn" and "warn" in frag[1] for frag in warn_ft
    )
    err = to_plain_text(format_toolbar_model_segment("gpt-4o", 117_760, plain=True))
    assert "8% context left" in err
    assert "err" in err
    err_ft = format_toolbar_model_segment("gpt-4o", 117_760, plain=True)
    assert any(
        frag[0] == "class:bottom-toolbar.err" and "err" in frag[1] for frag in err_ft
    )
    assert "Key rejected" in to_plain_text(
        format_toolbar_model_segment("gpt-4o", 0, key_rejected=True, plain=True)
    )

    boot = bootstrap(plain=True)
    client = FakeClient()
    client.queue_turn([DoneEvent(re="r1", reason="answered")])

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)

    with create_pipe_input() as pipe_in:
        pipe_in.send_text("   \r")
        pipe_in.send_text("line1\x1b\rline2\r")
        pipe_in.send_text("/exit\r")
        rc = await _run_chat(
            boot,
            client=client,
            console=console,
            input_dev=pipe_in,
        )

    assert rc == 0
    assert client.turns == ["line1\nline2"]


async def test_turn_key_listener_ignores_arrow_sequences_and_flushes_bare_escape() -> (
    None
):
    """TurnKeyListener ignores \\x1b[A arrow sequence and flushes bare \\x1b."""
    escape_calls = 0
    ctrl_c_calls = 0

    async def on_esc() -> None:
        nonlocal escape_calls
        escape_calls += 1

    async def on_ctrl_c() -> None:
        nonlocal ctrl_c_calls
        ctrl_c_calls += 1

    with create_pipe_input() as pipe_in:
        async with TurnKeyListener(
            pipe_in,
            on_escape=on_esc,
            on_ctrl_c=on_ctrl_c,
            escape_delay=0.04,
        ):
            pipe_in.send_text("\x1b[A")
            await asyncio.sleep(0.08)
            assert escape_calls == 0

            pipe_in.send_text("\x1b")
            await asyncio.sleep(0.08)
            assert escape_calls == 1
            assert ctrl_c_calls == 0


async def test_ctrl_c_once_warns_twice_quits_and_ctrl_d_saves_and_exits(
    aldwin_home: Path,
) -> None:
    """Ctrl-C once warns, twice quits; Ctrl-D saves and exits 0."""
    _ = aldwin_home
    boot = bootstrap(plain=True)

    # Case A: Ctrl-C once warns, second Ctrl-C exits
    client_a = FakeClient()
    buf_a = io.StringIO()
    console_a = create_console(plain=True, width=80, record=True, file=buf_a)
    with create_pipe_input() as pipe_a:
        pipe_a.send_text("\x03")
        pipe_a.send_text("\x03")
        rc_a = await _run_chat(
            boot,
            client=client_a,
            console=console_a,
            input_dev=pipe_a,
        )
    assert rc_a == 0
    assert client_a.closed is True
    text_a = console_a.export_text()
    # Ctrl+C hint stays in the dock footer, not scrollback.
    assert "Press Ctrl-C again to quit" not in text_a
    assert "Saved. See you." in text_a

    # Case B: Ctrl-D (EOF) saves and exits
    client_b = FakeClient()
    buf_b = io.StringIO()
    console_b = create_console(plain=True, width=80, record=True, file=buf_b)
    with create_pipe_input() as pipe_b:
        pipe_b.send_text("\x04")
        rc_b = await _run_chat(
            boot,
            client=client_b,
            console=console_b,
            input_dev=pipe_b,
        )
    assert rc_b == 0
    assert client_b.closed is True
    assert "Saved. See you." in console_b.export_text()


async def test_chat_slash_command_status_and_error_events(aldwin_home: Path) -> None:
    """`run_chat` routes slash commands and renders `StatusEvent` and `ErrorEvent`."""
    from aldwin.protocol import ErrorEvent, StatusEvent

    _ = aldwin_home
    boot = bootstrap(plain=True)
    client = FakeClient()
    client.queue_turn(
        [
            StatusEvent(re="r1", message="OpenAI is busy. Retrying in 1s (1/3)…"),
            ErrorEvent(re="r1", code="rate_limited", message="rate limit"),
        ]
    )

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    with create_pipe_input() as pipe_in:
        pipe_in.send_text("/help\r")
        pipe_in.send_text("trigger retry and error\r")
        pipe_in.send_text("/exit\r")
        rc = await _run_chat(
            boot,
            client=client,
            console=console,
            input_dev=pipe_in,
        )

    assert rc == 0
    text = console.export_text()
    assert "Commands:" in text
    # Retry status updates in-place (not stacked into scrollback in plain mode).
    assert text.count("OpenAI is busy. Retrying") == 0
    assert "OpenAI is rate-limiting requests right now." in text


async def test_ctrl_c_during_turn_stops_then_second_ctrl_c_quits(
    aldwin_home: Path,
) -> None:
    """Ctrl-C during a turn stops the turn; second Ctrl-C saves and quits."""
    _ = aldwin_home
    boot = bootstrap(plain=True)
    client = FakeClient()
    client.hang_until_stopped = True
    client.queue_turn([TextEvent(re="r1", delta="Streaming...")])

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    with create_pipe_input() as pipe_in:
        pipe_in.send_text("work on something\r")

        async def _send_ctrl_c_twice() -> None:
            await client.turn_started_event.wait()
            await asyncio.sleep(0.02)
            pipe_in.send_text("\x03")
            await client._stop_gate.wait()
            await asyncio.sleep(0.05)
            pipe_in.send_text("\x03")

        feeder = asyncio.create_task(_send_ctrl_c_twice())
        rc = await _run_chat(
            boot,
            client=client,
            console=console,
            input_dev=pipe_in,
        )
        await feeder

    assert rc == 0
    assert client.stop_calls >= 1
    assert client.closed is True
    text = console.export_text()
    # Quit hint is footer-only; second Ctrl+C still exits.
    assert "Saved. See you." in text


async def test_backslash_enter_and_ctrl_j_insert_newlines_and_plain_style(
    aldwin_home: Path,
) -> None:
    """`\\`+Enter and `Ctrl+J` insert newlines; plain is `noreverse`."""
    with create_pipe_input() as pipe_in:
        session = build_prompt_session(
            aldwin_home,
            toolbar_fn=lambda: "toolbar",
            plain=True,
            input_dev=pipe_in,
        )
        assert session.style is PLAIN_PROMPT_STYLE

        pipe_in.send_text("first line\\\rsecond line\x0athird line\r")
        result = await session.prompt_async("❯ ")
        assert result == "first line\nsecond line\nthird line"


async def test_approval_keys_default_no_ctrl_o_session_grant_and_decline_reason(
    aldwin_home: Path,
) -> None:
    """`Ctrl+O` expands, `a` grants session, `n` collects reason."""
    _ = aldwin_home
    expanded_count = 0

    def _on_expand() -> None:
        nonlocal expanded_count
        expanded_count += 1

    with create_pipe_input() as pipe_a:
        pipe_a.send_text("\x0fa")
        ans_a = await asyncio.wait_for(
            read_approval_key(pipe_a, grantable=True, on_expand=_on_expand),
            timeout=0.5,
        )
        assert ans_a == "yes_session"
        assert expanded_count == 1

    # When `grantable=False`, pressing `a` is ignored until a valid key arrives
    with create_pipe_input() as pipe_no_grant:
        pipe_no_grant.send_text("ay")
        ans_ng = await asyncio.wait_for(
            read_approval_key(pipe_no_grant, grantable=False),
            timeout=0.5,
        )
        assert ans_ng == "yes"

    # Full chat loop: user presses `n` and enters a reason
    boot = bootstrap(plain=True)
    client = FakeClient()
    client.queue_turn(
        [
            ApprovalEvent(
                re="r1",
                approval_id="ap_edit",
                tool="edit_file",
                tier="diff",
                summary="Edit notes.md",
                detail="--- a/notes.md\n+++ b/notes.md\n-old\n+new",
                grantable=True,
                grant_scope="file",
                digest="sha256:1234",
            ),
            DoneEvent(re="r1", reason="answered"),
        ]
    )

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    with create_pipe_input() as pipe_in:
        pipe_in.send_text("fix notes\r")

        async def _decline_with_reason() -> None:
            await client.approval_emitted_event.wait()
            # The answer, the reason, and /exit can arrive in one read.
            pipe_in.send_text("nonly fix the heading\r/exit\r")

        feeder = asyncio.create_task(_decline_with_reason())
        rc = await _run_chat(
            boot,
            client=client,
            console=console,
            input_dev=pipe_in,
        )
        await feeder

    assert rc == 0
    assert client.approval_details == [
        ("ap_edit", "no", "sha256:1234", "only fix the heading")
    ]
    assert "✗ declined" in console.export_text()


async def test_invalid_key_passes_key_rejected_to_toolbar(aldwin_home: Path) -> None:
    """Rejected key check sets the footer key_rejected flag before prompts."""
    _ = aldwin_home
    boot = bootstrap(plain=True)
    client = FakeClient()
    seen_rejected: list[bool] = []
    real = format_toolbar_model_segment

    def _capture(
        model: str,
        used_tokens: int = 0,
        *,
        todo_label: str | None = None,
        plain: bool = False,
        key_rejected: bool = False,
    ) -> object:
        seen_rejected.append(key_rejected)
        return real(
            model,
            used_tokens,
            todo_label=todo_label,
            plain=plain,
            key_rejected=key_rejected,
        )

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    with (
        create_pipe_input() as pipe_in,
        patch(
            "aldwin.cli.chat_screen.format_toolbar_model_segment",
            side_effect=_capture,
        ),
    ):
        pipe_in.send_text("/exit\r")
        rc = await _run_chat(
            boot,
            client=client,
            console=console,
            input_dev=pipe_in,
            key_status="invalid",
        )

    assert rc == 0
    assert True in seen_rejected


async def test_context_nearly_full_notice_once(aldwin_home: Path) -> None:
    """After a turn that leaves ≤10% context, print the one-shot notice once."""
    _ = aldwin_home
    boot = bootstrap(plain=True)
    client = FakeClient()
    # gpt-4o 128k → ~8% left with 117_760 used tokens.
    client.queue_turn(
        [
            DoneEvent(
                re="r1",
                reason="answered",
                usage=TokenUsage(input_tokens=117_760, output_tokens=0),
            )
        ]
    )

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    with create_pipe_input() as pipe_in:
        pipe_in.send_text("fill context\r")
        pipe_in.send_text("/exit\r")
        rc = await _run_chat(
            boot,
            client=client,
            console=console,
            input_dev=pipe_in,
        )

    assert rc == 0
    text = console.export_text()
    assert text.count(CONTEXT_NEARLY_FULL_NOTICE) == 1


def test_whats_new_due_matrix(aldwin_home: Path) -> None:
    """TC-13: absent, older, unparseable, and current last_seen_version."""
    assert whats_new_due(aldwin_home, "0.0.2") is True

    mark_whats_new_seen(aldwin_home, "0.0.1")
    assert whats_new_due(aldwin_home, "0.0.2") is True

    mark_whats_new_seen(aldwin_home, "banana")
    assert whats_new_due(aldwin_home, "0.0.2") is True

    mark_whats_new_seen(aldwin_home, "0.0.2")
    assert whats_new_due(aldwin_home, "0.0.2") is False


def test_mark_whats_new_seen_persists_and_preserves_comments(aldwin_home: Path) -> None:
    """TC-14: mark_whats_new_seen writes last_seen_version and keeps comments."""
    config_path = aldwin_home / "config.yaml"
    before = config_path.read_text(encoding="utf-8")
    assert "# Aldwin configuration" in before

    mark_whats_new_seen(aldwin_home, "0.0.2")

    after = config_path.read_text(encoding="utf-8")
    assert "# Aldwin configuration" in after
    assert "last_seen_version: 0.0.2" in after
    assert load_config(aldwin_home).last_seen_version == "0.0.2"
    assert whats_new_due(aldwin_home, "0.0.2") is False


def test_render_whats_new_if_due_shows_once(aldwin_home: Path) -> None:
    """TC-15: the panel shows on the first due launch, not on the next."""
    buf = io.StringIO()
    console = create_console(plain=False, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=False)

    shown = render_whats_new_if_due(
        renderer, home=aldwin_home, current_version="0.0.2", boot_created=False
    )
    assert shown is True
    assert "What's new" in console.export_text()

    buf2 = io.StringIO()
    console2 = create_console(plain=False, width=80, record=True, file=buf2)
    renderer2 = ChatRenderer(console2, plain=False)
    shown_again = render_whats_new_if_due(
        renderer2, home=aldwin_home, current_version="0.0.2", boot_created=False
    )
    assert shown_again is False
    assert "What's new" not in console2.export_text()


def test_render_whats_new_if_due_skips_new_homes(aldwin_home: Path) -> None:
    """TC-16: a freshly created home (``boot_created``) never shows the panel."""
    buf = io.StringIO()
    console = create_console(plain=False, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=False)

    shown = render_whats_new_if_due(
        renderer, home=aldwin_home, current_version="0.0.2", boot_created=True
    )
    assert shown is False
    assert "What's new" not in console.export_text()
    assert load_config(aldwin_home).last_seen_version is None


def test_render_whats_new_if_due_fails_quiet(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write failure while marking the panel seen never crashes the chat loop."""
    import aldwin.cli.chat_screen as chat_screen_mod

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(chat_screen_mod, "mark_whats_new_seen", _boom)

    buf = io.StringIO()
    console = create_console(plain=False, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=False)

    shown = render_whats_new_if_due(
        renderer, home=aldwin_home, current_version="0.0.2", boot_created=False
    )
    assert shown is False


def test_render_whats_new_copy_and_plain_mode() -> None:
    """TC-17: the panel prints the verbatim copy; plain mode drops border colour."""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    renderer.render_whats_new(WHATS_NEW_TEXT, version="0.0.2")
    text = console.export_text()
    assert "What's new in 0.0.2" in text
    assert "pick with @" in text
    assert ".env" in text
    assert "~/.ssh" in text
