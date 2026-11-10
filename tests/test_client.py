"""Tests for `aldwin.client.AldwinClient` (`FR-16`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from agentling import Agent, ChatMessage, ToolCall
from agentling.memory import ActionStep, TaskStep, ToolResult
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.home import ensure_home
from aldwin.host import AldwinHost, InProcessTransport, SessionStore
from aldwin.protocol import DoneEvent, Event, HistoryItem, TextEvent


async def test_client_history_and_new_conversation_lifecycle(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """TC-12, TC-26, TC-28: `history`, `new_conversation`, and idle `stop`."""
    ensure_home(aldwin_home)
    fake_model.add_turns(
        FakeTurn.text(["First reply."]),
        FakeTurn.text(["Second reply."]),
        FakeTurn.text(["Fresh session reply."]),
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()

        idle_stop = await client.stop()
        assert isinstance(idle_stop, DoneEvent)
        assert idle_stop.reason == "ok"

        async for _ev in client.turn("Hello 1"):
            pass
        async for _ev in client.turn("Hello 2"):
            pass

        first_session_path = host.stored_session.path
        hist = await client.history(limit=20)
        assert hist.interrupted is False
        assert hist.messages == [
            HistoryItem(role="user", text="Hello 1"),
            HistoryItem(role="assistant", text="First reply."),
            HistoryItem(role="user", text="Hello 2"),
            HistoryItem(role="assistant", text="Second reply."),
        ]

        await client.new_conversation()
        assert host.stored_session.path != first_session_path
        assert first_session_path.exists()

        fresh_hist = await client.history(limit=20)
        assert fresh_hist.messages == []
        assert fresh_hist.interrupted is False

        async for _ev in client.turn("Hello fresh"):
            pass
        updated_hist = await client.history(limit=20)
        assert updated_hist.messages == [
            HistoryItem(role="user", text="Hello fresh"),
            HistoryItem(role="assistant", text="Fresh session reply."),
        ]
    finally:
        await client.close()
        await host.stop_host()


async def test_client_resumes_interrupted_session_on_host_restart(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """TC-11: Host restart loads interrupted session and reports `interrupted=True`."""
    ensure_home(aldwin_home)
    store = SessionStore()
    stored = store.create_new(model="gpt-4o")
    stored.memory.add(TaskStep(task="Cut off mid-work"))
    stored.memory.add(
        ActionStep(
            model_message=ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="web_search", arguments={"query": "python"})
                ],
            ),
            tool_results=[
                ToolResult(
                    tool_call_id="c1",
                    name="web_search",
                    content="5 results\n1. Python.org",
                )
            ],
        )
    )
    store.save(stored, stored.memory, interrupted=True)

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        hist = await client.history(limit=20)
        assert hist.interrupted is True
        assert hist.messages == [
            HistoryItem(role="user", text="Cut off mid-work"),
            HistoryItem(role="tool", text="✓ web_search · 5 results"),
        ]
    finally:
        await client.close()
        await host.stop_host()


async def test_client_cleans_up_queue_when_caller_breaks_early(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """TC-29: Breaking out of `client.turn()` early cleans up the per-request queue."""
    ensure_home(aldwin_home)
    fake_model.add_turns(FakeTurn.text(["chunk1", "chunk2", "chunk3"]))

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        seen: list[Event] = []
        async for ev in client.turn("Stream three chunks"):
            seen.append(ev)
            if isinstance(ev, TextEvent):
                break

        assert len(seen) == 1
        assert isinstance(seen[0], TextEvent)
        await client.stop()
        assert client._queues == {}

        hist = await client.history(limit=5)
        assert len(hist.messages) >= 1
    finally:
        await client.close()
        await host.stop_host()


async def test_open_in_process_client_and_inspect_helpers(
    aldwin_home: Path,
) -> None:
    """`open_in_process_client` and client inspect helpers expose host state."""
    from aldwin.client import open_in_process_client, save_setup

    save_setup(
        openai_api_key="sk-proj-test12345678",
        tavily_api_key="tvly-dev-test87654321",
        model="gpt-4o",
        theme="light",
        last_seen_version="0.0.2",
        home=aldwin_home,
    )
    from aldwin.core.settings import load_config

    cfg = load_config(aldwin_home)
    assert cfg.theme == "light"
    assert cfg.last_seen_version == "0.0.2"

    async with open_in_process_client(home=aldwin_home) as client:
        # Calling connect() a second time is idempotent and returns cached DoneEvent
        second_hello = await client.connect()
        assert isinstance(second_hello, DoneEvent)
        assert second_hello.reason == "ok"
        assert client.model == "openai/gpt-4o"
        assert client.session_id
        assert client.updated_at is not None

        persona = client.persona_summary()
        assert persona.size_chars > 0
        mem = client.memory_summary()
        assert mem.profile_cap_chars == 4000
        skills = client.skills_report()
        assert any(s.name == "using-aldwin" for s in skills.valid)
        tools = client.tools_summary()
        assert len(tools) == 18
        assert {t.name for t in tools} >= {"todo", "calculate"}


async def test_in_process_key_check_wrapper(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`check_openai_key` maps `core.models` results."""
    import aldwin.core.models as core_models
    from aldwin.client import check_openai_key

    statuses = iter(
        [
            core_models.KeyStatus.OK,
            core_models.KeyStatus.NO_ACCESS,
            core_models.KeyStatus.NETWORK_ERROR,
            core_models.KeyStatus.INVALID,
        ]
    )

    async def fake_check_key(_key: str) -> core_models.KeyStatus:
        return next(statuses)

    monkeypatch.setattr(core_models, "check_key", fake_check_key)
    assert await check_openai_key("k") == "ok"
    assert await check_openai_key("k") == "no_access"
    assert await check_openai_key("k") == "network_error"
    assert await check_openai_key("k") == "invalid"


async def test_verify_active_openai_key_uses_home_secret(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`verify_active_openai_key` resolves the home key and maps check results."""
    from pydantic import SecretStr

    import aldwin.core.models as core_models
    from aldwin.client import verify_active_openai_key
    from aldwin.core.home import ensure_home
    from aldwin.core.settings import SecretsConfig, save_secrets

    ensure_home(aldwin_home)
    save_secrets(
        SecretsConfig(openai_api_key=SecretStr("sk-test-key-for-verify")),
        home=aldwin_home,
    )

    async def fake_check_key(key: str) -> core_models.KeyStatus:
        assert key == "sk-test-key-for-verify"
        return core_models.KeyStatus.INVALID

    monkeypatch.setattr(core_models, "check_key", fake_check_key)
    assert await verify_active_openai_key(aldwin_home) == "invalid"

    save_secrets(SecretsConfig(openai_api_key=SecretStr("")), home=aldwin_home)
    assert await verify_active_openai_key(aldwin_home) == "invalid"


async def test_client_status_reports_an_idle_host(host_client: AldwinClient) -> None:
    """The client's status call reaches the host and returns empty progress."""
    status = await host_client.status()

    assert status.turn_streamed_chunks == 0
    assert status.active_tool is None and status.partial_text is None
