"""Host skill picker wiring: list/show/used, inject, history."""

from __future__ import annotations

from pathlib import Path

from agentling.memory import TaskStep
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.home import ensure_home
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import (
    ApprovalEvent,
    DoneEvent,
    SkillBodyEvent,
    SkillsEvent,
    SkillsUsedEvent,
    TextEvent,
    ToolResultEvent,
)


def _builder(fake_model: FakeModel, home: Path):
    def build(model_name: str, ask):  # noqa: ANN001
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=home,
            model=fake_model,
        )

    return build


async def test_skills_list_includes_defaults_and_flags(aldwin_home: Path) -> None:
    ensure_home(aldwin_home)
    fake_model = FakeModel()
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        home=aldwin_home,
        agent_builder=_builder(fake_model, aldwin_home),
    )
    await host.start()
    client = AldwinClient(transport.connect())
    try:
        await client.connect()
        event = await client.skills_list()
        assert isinstance(event, SkillsEvent)
        names = {item.name for item in event.items}
        assert "files" in names
        assert "using-aldwin" in names
        files = next(i for i in event.items if i.name == "files")
        assert files.source == "default"
        assert isinstance(files.flags, list)
    finally:
        await client.close()
        await host.stop_host()


async def test_skills_show_known_and_unknown(aldwin_home: Path) -> None:
    ensure_home(aldwin_home)
    fake_model = FakeModel()
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        home=aldwin_home,
        agent_builder=_builder(fake_model, aldwin_home),
    )
    await host.start()
    client = AldwinClient(transport.connect())
    try:
        await client.connect()
        body = await client.skills_show("files")
        assert isinstance(body, SkillBodyEvent)
        assert body.name == "files"
        assert body.body.strip()
        try:
            await client.skills_show("nope-not-real")
            raise AssertionError("expected ProtocolError")
        except Exception as exc:  # noqa: BLE001
            assert "No skill named" in str(exc)
    finally:
        await client.close()
        await host.stop_host()


async def test_turn_injects_skill_emits_used_and_history_is_clean(
    aldwin_home: Path,
) -> None:
    ensure_home(aldwin_home)
    fake_model = FakeModel()
    fake_model.add_turns(FakeTurn.text("done"))
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        home=aldwin_home,
        agent_builder=_builder(fake_model, aldwin_home),
    )
    await host.start()
    client = AldwinClient(transport.connect())
    try:
        await client.connect()
        events = [ev async for ev in client.turn("Tidy this @files", skills=["files"])]
        used = next(ev for ev in events if isinstance(ev, SkillsUsedEvent))
        assert used.names == ["files"]
        assert used.dropped == []
        assert any(isinstance(ev, TextEvent) for ev in events)
        assert isinstance(events[-1], DoneEvent)

        memory = host._agent_session.memory
        task_steps = [s for s in memory.steps if isinstance(s, TaskStep)]
        assert task_steps
        assert "<picked_skills>" in task_steps[0].task
        assert '<skill name="files">' in task_steps[0].task
        assert "Tidy this @files" in task_steps[0].task

        messages = host._session_store.render_history(
            host._agent_session.memory,
            turns=host._stored_session.turns,
        )
        user_texts = [m.text for m in messages if m.role == "user"]
        assert user_texts
        assert user_texts[-1] == "Tidy this @files"
        assert "<picked_skills>" not in user_texts[-1]
        assert host._stored_session.turns is not None
        assert host._stored_session.turns[-1]["skills"] == ["files"]
    finally:
        await client.close()
        await host.stop_host()


async def test_turn_drops_unknown_and_over_limit(aldwin_home: Path) -> None:
    ensure_home(aldwin_home)
    fake_model = FakeModel()
    fake_model.add_turns(FakeTurn.text("ok"))
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        home=aldwin_home,
        agent_builder=_builder(fake_model, aldwin_home),
    )
    await host.start()
    client = AldwinClient(transport.connect())
    try:
        await client.connect()
        events = [
            ev
            async for ev in client.turn(
                "x",
                skills=["files", "memory", "terminal", "web-research", "gone"],
            )
        ]
        used = next(ev for ev in events if isinstance(ev, SkillsUsedEvent))
        assert used.names == ["files", "memory", "terminal"]
        reasons = {d.name: d.reason for d in used.dropped}
        assert reasons["web-research"] == "limit"
        assert reasons["gone"] == "unknown"
    finally:
        await client.close()
        await host.stop_host()


async def test_session_title_uses_original_user_text(aldwin_home: Path) -> None:
    ensure_home(aldwin_home)
    fake_model = FakeModel()
    fake_model.add_turns(FakeTurn.text("ok"))
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        home=aldwin_home,
        agent_builder=_builder(fake_model, aldwin_home),
    )
    await host.start()
    client = AldwinClient(transport.connect())
    try:
        await client.connect()
        async for _ in client.turn("Tidy this @files", skills=["files"]):
            pass
        title = host._stored_session.meta.title
        assert title.startswith("Tidy this @files")
        assert "<picked_skills>" not in title
    finally:
        await client.close()
        await host.stop_host()


async def test_picked_skill_does_not_widen_hardline(
    aldwin_home: Path, tmp_path: Path, fake_model: FakeModel
) -> None:
    """`@terminal` injects guidance; hardline still refuses `mkfs` without ask."""
    ensure_home(aldwin_home)
    ws = tmp_path / "ws"
    ws.mkdir()
    fake_model.add_turns(
        FakeTurn.tool_call(
            "run_command",
            {"command": "mkfs.ext4 /tmp/aldwin-hardline-probe"},
            call_id="c_mkfs",
        ),
        FakeTurn.text(["blocked"]),
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        home=aldwin_home,
        workspace=ws,
        agent_builder=_builder(fake_model, aldwin_home),
    )
    await host.start()
    client = AldwinClient(transport.connect())
    try:
        await client.connect()
        events = [
            ev
            async for ev in client.turn(
                "format disk @terminal",
                skills=["terminal"],
            )
        ]
        assert any(isinstance(ev, SkillsUsedEvent) for ev in events)
        assert not any(isinstance(ev, ApprovalEvent) for ev in events)
        results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
        assert results
        assert results[0].ok is False
        summary = results[0].summary.lower()
        assert "never" in summary or "block" in summary
    finally:
        await client.close()
        await host.stop_host()


async def test_next_turn_without_skills_is_plain(aldwin_home: Path) -> None:
    ensure_home(aldwin_home)
    fake_model = FakeModel()
    fake_model.add_turns(FakeTurn.text("a"), FakeTurn.text("b"))
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        home=aldwin_home,
        agent_builder=_builder(fake_model, aldwin_home),
    )
    await host.start()
    client = AldwinClient(transport.connect())
    try:
        await client.connect()
        async for _ in client.turn("first @files", skills=["files"]):
            pass
        async for _ in client.turn("hello", skills=[]):
            pass
        tasks = [
            s.task for s in host._agent_session.memory.steps if isinstance(s, TaskStep)
        ]
        assert "<picked_skills>" in tasks[0]
        assert tasks[1] == "hello"
    finally:
        await client.close()
        await host.stop_host()
