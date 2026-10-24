"""Writers in one step show at most one outstanding approval."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import ApprovalEvent, ToolResultEvent


@pytest.fixture
async def seq_host(
    fake_model: FakeModel,
    aldwin_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AldwinClient, FakeModel, Path, AldwinHost]]:
    """In-process host with a temp workspace and the fake model."""
    root = tmp_path / "ws"
    root.mkdir()
    real_build = build_agent

    def _build(config, secrets, ask, **kwargs):
        kwargs["model"] = fake_model
        return real_build(config, secrets, ask, **kwargs)

    monkeypatch.setattr("aldwin.host.runner.build_agent", _build)
    transport = InProcessTransport()
    host = AldwinHost(transport, home=aldwin_home, workspace=root)
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        yield client, fake_model, root, host
    finally:
        await client.close()
        await host.stop_host()


async def test_edit_and_write_one_step_sequential(seq_host) -> None:
    """edit_file + write_file: one approval at a time; both yes writes both."""
    client, model, root, host = seq_host
    (root / "a.md").write_text("alpha\n", encoding="utf-8")
    host.policy_session.mark_read(root / "a.md")
    model.add_turns(
        FakeTurn(
            tool_calls=[
                (
                    "c1",
                    "edit_file",
                    {"path": "a.md", "old": "alpha", "new": "ALPHA"},
                ),
                (
                    "c2",
                    "write_file",
                    {"path": "b.md", "content": "beta\n"},
                ),
            ]
        ),
        FakeTurn.text(["done"]),
    )
    pending: str | None = None
    approvals: list[ApprovalEvent] = []
    async for event in client.turn("edit and write"):
        if isinstance(event, ApprovalEvent):
            assert pending is None, "two approvals pending at once"
            pending = event.approval_id
            approvals.append(event)
            await client.approve(event.approval_id, "yes", digest=event.digest)
            pending = None
    assert len(approvals) == 2
    assert (root / "a.md").read_text(encoding="utf-8") == "ALPHA\n"
    assert (root / "b.md").read_text(encoding="utf-8") == "beta\n"


async def test_no_to_either_denies_batch(seq_host) -> None:
    """A no to the second writer leaves b.md unwritten."""
    client, model, root, host = seq_host
    (root / "a.md").write_text("alpha\n", encoding="utf-8")
    host.policy_session.mark_read(root / "a.md")
    model.add_turns(
        FakeTurn(
            tool_calls=[
                (
                    "c1",
                    "edit_file",
                    {"path": "a.md", "old": "alpha", "new": "ALPHA"},
                ),
                (
                    "c2",
                    "write_file",
                    {"path": "b.md", "content": "beta\n"},
                ),
            ]
        ),
        FakeTurn.text(["stopped"]),
    )
    n = 0
    results: list[ToolResultEvent] = []
    async for event in client.turn("edit then decline write"):
        if isinstance(event, ApprovalEvent):
            n += 1
            answer = "yes" if n == 1 else "no"
            await client.approve(event.approval_id, answer, digest=event.digest)
        if isinstance(event, ToolResultEvent):
            results.append(event)
    assert n == 2
    # A no to either denies both writers in the batch.
    assert (root / "a.md").read_text(encoding="utf-8") == "alpha\n"
    assert not (root / "b.md").exists()
    assert any(not r.ok for r in results)


async def test_no_to_first_denies_batch(seq_host) -> None:
    """A no to the first writer leaves neither file changed."""
    client, model, root, host = seq_host
    (root / "a.md").write_text("alpha\n", encoding="utf-8")
    host.policy_session.mark_read(root / "a.md")
    model.add_turns(
        FakeTurn(
            tool_calls=[
                (
                    "c1",
                    "edit_file",
                    {"path": "a.md", "old": "alpha", "new": "ALPHA"},
                ),
                (
                    "c2",
                    "write_file",
                    {"path": "b.md", "content": "beta\n"},
                ),
            ]
        ),
        FakeTurn.text(["stopped"]),
    )
    n = 0
    async for event in client.turn("decline first edit"):
        if isinstance(event, ApprovalEvent):
            n += 1
            await client.approve(event.approval_id, "no", digest=event.digest)
    assert n >= 1
    assert (root / "a.md").read_text(encoding="utf-8") == "alpha\n"
    assert not (root / "b.md").exists()
