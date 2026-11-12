"""Host display emission, validation, sanitising, and CLI compatibility tests."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

import pytest
from agentling import Tool
from conftest import auto_approve
from fakes.model import FakeModel, FakeTurn

from aldwin.cli.render import ChatRenderer
from aldwin.cli.theme import create_console
from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.policy import wrap_tools_with_policy
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.core.tools import ToolOutput
from aldwin.core.workspace import Workspace
from aldwin.host import AldwinHost, AskFn, InProcessTransport
from aldwin.protocol import (
    ApprovalEvent,
    DoneEvent,
    TerminalDisplay,
    ToolCallEvent,
    ToolResultEvent,
)


async def test_events_carry_display_and_matching_call_id(
    fake_model: FakeModel,
    host_client: AldwinClient,
) -> None:
    """`run_command('echo hi')` emits matching `call_id` and `terminal` displays."""
    fake_model.add_turns(
        FakeTurn.tool_call("run_command", {"command": "echo hi"}, call_id="c_echo"),
        FakeTurn.final("done"),
    )

    events = await auto_approve(host_client, host_client.turn("say hi"))

    call_ev = next(e for e in events if isinstance(e, ToolCallEvent))
    approval_ev = next(e for e in events if isinstance(e, ApprovalEvent))
    result_ev = next(e for e in events if isinstance(e, ToolResultEvent))

    assert approval_ev.call_id == call_ev.call_id == "c_echo"
    assert isinstance(approval_ev.display, TerminalDisplay)
    assert approval_ev.display.command == "echo hi"
    assert approval_ev.display.stdout == ""

    assert isinstance(result_ev.display, TerminalDisplay)
    assert result_ev.display.stdout == "hi\n"
    assert result_ev.display.exit_code == 0


async def test_invalid_display_is_dropped_with_one_warning(
    fake_model: FakeModel,
    aldwin_home: Path,
    workspace: Workspace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unknown `display.kind` logs a single WARNING and emits `display=None`."""

    class _BadDisplayTool(Tool):
        name = "calculate"
        description = "Returns an invalid display."
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        }

        async def forward(self, expression: str) -> ToolOutput:  # type: ignore[override]
            return ToolOutput(
                expression,
                summary="4",
                ok=True,
                display={"kind": "chart"},
            )

    def builder(model_name: str, ask: AskFn) -> Any:
        from aldwin.core.agent import coerce_policy_ask
        from aldwin.core.policy import PolicySession

        sess = PolicySession(workspace=workspace, home=aldwin_home)
        wrapped = wrap_tools_with_policy(
            [_BadDisplayTool()],
            session=sess,
            ask=coerce_policy_ask(ask),
        )
        agent = build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            workspace=workspace,
            policy_session=sess,
            model=fake_model,
            tools=wrapped,
        )
        setattr(agent, "_test_policy_session", sess)
        return agent

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder, home=aldwin_home)
    host._policy_session = getattr(host._agent, "_test_policy_session")
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        fake_model.add_turns(
            FakeTurn.tool_call("calculate", {"expression": "2+2"}, call_id="c_bad"),
            FakeTurn.final("4"),
        )
        with caplog.at_level(logging.WARNING, logger="aldwin.host.display"):
            events = await auto_approve(client, client.turn("calc"))
    finally:
        await client.close()
        await host.stop_host()

    result_ev = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result_ev.ok is True
    assert result_ev.display is None

    warnings = [
        rec
        for rec in caplog.records
        if rec.name == "aldwin.host.display" and rec.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "calculate" in warnings[0].getMessage()


async def test_display_text_strips_ansi_bidi_and_control_characters(
    fake_model: FakeModel,
    host_client: AldwinClient,
) -> None:
    """ANSI escapes, bidi overrides, and control bytes are stripped before emit."""
    fake_model.add_turns(
        FakeTurn.tool_call(
            "run_command",
            {"command": "printf '\\033[31mred\\033[0m \\342\\200\\256bidi \\007bel'"},
            call_id="c_ctrl",
        ),
        FakeTurn.final("cleaned"),
    )

    events = await auto_approve(host_client, host_client.turn("print controls"))
    result_ev = next(e for e in events if isinstance(e, ToolResultEvent))
    assert isinstance(result_ev.display, TerminalDisplay)
    stdout = result_ev.display.stdout
    assert "\x1b" not in stdout
    assert "\u202e" not in stdout
    assert "\x07" not in stdout
    assert "red" in stdout and "bidi" in stdout and "bel" in stdout


async def test_cli_approval_panel_render_snapshot_is_byte_identical(
    fake_model: FakeModel,
    host_client: AldwinClient,
) -> None:
    """`ApprovalEvent` with `call_id` and `display` renders identically in the CLI."""
    fake_model.add_turns(
        FakeTurn.tool_call("run_command", {"command": "echo snapshot"}, call_id="c_1"),
        FakeTurn.final("done"),
    )

    events = await auto_approve(host_client, host_client.turn("snap"))
    approval_ev = next(e for e in events if isinstance(e, ApprovalEvent))
    assert approval_ev.call_id == "c_1"
    assert approval_ev.display is not None

    legacy_ev = approval_ev.model_copy(update={"call_id": None, "display": None})

    def _render_panel(ev: ApprovalEvent) -> str:
        buf = io.StringIO()
        console = create_console(width=80, record=True, file=buf)
        renderer = ChatRenderer(console)
        turn = renderer.start_turn()
        turn.pause_for_approval(ev)
        turn.resolve_approval(ev, "yes")
        turn.finish_done(DoneEvent(re=ev.re, reason="answered"))
        return console.export_text()

    assert _render_panel(approval_ev) == _render_panel(legacy_ev)


async def test_failed_command_keeps_its_terminal_display(
    fake_model: FakeModel,
    host_client: AldwinClient,
) -> None:
    """`run_command('exit 1')` emits `ok=False` while keeping `display.exit_code=1`."""
    fake_model.add_turns(
        FakeTurn.tool_call("run_command", {"command": "exit 1"}, call_id="c_fail"),
        FakeTurn.final("failed"),
    )

    events = await auto_approve(host_client, host_client.turn("fail"))
    result_ev = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result_ev.ok is False
    assert isinstance(result_ev.display, TerminalDisplay)
    assert result_ev.display.exit_code == 1


def test_non_dict_display_is_dropped_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A display that is not a dict is dropped and named in a single warning."""
    from aldwin.host.display import to_display

    with caplog.at_level(logging.WARNING, logger="aldwin.host.display"):
        assert to_display("oops", tool="calculate") is None
        assert to_display(None, tool="calculate") is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "calculate" in warnings[0].getMessage()
