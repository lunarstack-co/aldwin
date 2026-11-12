"""Security tests for secret redaction across displays and lean approval logs."""

from __future__ import annotations

from pathlib import Path

from conftest import auto_approve
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.core.tools import display as core_display
from aldwin.core.workspace import Workspace
from aldwin.host import AldwinHost, AskFn, InProcessTransport
from aldwin.host.display import to_display

SAMPLE_KEY = "sk-live-abc123def456ghi789"


def test_secrets_redacted_across_every_display_field() -> None:
    """`to_display` replaces keys with `‹redacted›` in every display kind."""
    raws = [
        core_display.terminal(
            command="run",
            stdout=f"out {SAMPLE_KEY}\n",
            stderr=f"err {SAMPLE_KEY}\n",
        ),
        core_display.diff(
            path="notes.md",
            unified=(
                "--- a/notes.md\n+++ b/notes.md\n"
                f"@@ -1 +1 @@\n-old\n+key {SAMPLE_KEY}\n"
            ),
        ),
        core_display.table(
            headers=["secret"],
            rows=[[f"val {SAMPLE_KEY}"]],
        ),
        core_display.search(
            query="keys",
            results=[
                {
                    "title": "Doc",
                    "url": "https://example.com",
                    "snippet": f"found {SAMPLE_KEY} here",
                }
            ],
        ),
        core_display.page(
            url="https://example.com",
            title="Page",
            site="example.com",
            description=f"desc {SAMPLE_KEY}",
        ),
    ]

    for raw in raws:
        validated = to_display(raw, tool="test_tool")
        assert validated is not None
        dumped = validated.model_dump_json()
        assert SAMPLE_KEY not in dumped
        assert "‹redacted›" in dumped


async def test_approval_log_never_records_diff_lines_stdout_or_display_json(
    fake_model: FakeModel,
    aldwin_home: Path,
    workspace: Workspace,
) -> None:
    """`approvals.jsonl` stays metadata-only with no file diffs, stdout, or displays."""
    target = workspace.root / "notes.md"
    target.write_text("before_marker_991\n", encoding="utf-8")

    logger = ApprovalLogger(home=aldwin_home)

    def builder(model_name: str, ask: AskFn):
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            workspace=workspace,
            approval_log=logger,
            model=fake_model,
        )

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=builder,
        home=aldwin_home,
        workspace=workspace,
    )
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        fake_model.add_turns(
            FakeTurn.tool_call("read_file", {"path": "notes.md"}, call_id="c_read"),
            FakeTurn.tool_call(
                "edit_file",
                {
                    "path": "notes.md",
                    "old": "before_marker_991",
                    "new": "after_marker_992",
                },
                call_id="c_edit",
            ),
            FakeTurn.tool_call(
                "run_command",
                {"command": "echo stdout_marker_993"},
                call_id="c_cmd",
            ),
            FakeTurn.final("done"),
        )
        await auto_approve(client, client.turn("edit and run"))
    finally:
        await client.close()
        await host.stop_host()
        logger.close()

    log_path = aldwin_home / "logs" / "approvals.jsonl"
    log_text = log_path.read_text(encoding="utf-8")
    assert log_text.strip() != ""
    assert "before_marker_991" not in log_text
    assert "after_marker_992" not in log_text
    assert "stdout_marker_993\n" not in log_text
    assert '"kind":"diff"' not in log_text
    assert '"kind": "diff"' not in log_text
    assert '"kind":"terminal"' not in log_text
