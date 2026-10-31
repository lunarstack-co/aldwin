"""Sentinel tests for `tests/conftest.py` isolation, network guard, and fixtures."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import httpx
import pytest
from agentling import AgentSession, FinalEvent
from conftest import auto_approve, collect
from fakes.host import FakeHost
from fakes.model import FakeModel, final, hang, raise_, text, tool_call

from aldwin.core.home import home_dir
from aldwin.protocol import ApprovalEvent, DoneEvent, ErrorEvent, TextEvent


async def test_no_network_blocks_inet_sockets_and_allows_unix_socketpair() -> None:
    """TC-01: Outbound TCP/HTTP connections are blocked; local `socketpair()` works."""
    with pytest.raises(RuntimeError, match="Network access is disabled"):
        socket.create_connection(("93.184.216.34", 80), timeout=0.1)

    for family, addr in (
        (socket.AF_INET, ("93.184.216.34", 80)),
        (socket.AF_INET6, ("::1", 80)),
    ):
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            with pytest.raises(RuntimeError, match="Network access is disabled"):
                sock.connect(addr)
            with pytest.raises(RuntimeError, match="Network access is disabled"):
                sock.connect_ex(addr)
        finally:
            sock.close()

    async with httpx.AsyncClient() as client:
        with pytest.raises((RuntimeError, BaseExceptionGroup)) as exc_info:
            await client.get("https://93.184.216.34")
        assert "Network access is disabled" in str(exc_info.value) or any(
            "Network access is disabled" in str(sub)
            for sub in getattr(exc_info.value, "exceptions", ())
        )

    s1, s2 = socket.socketpair()
    try:
        s1.sendall(b"ping")
        assert s2.recv(4) == b"ping"
    finally:
        s1.close()
        s2.close()


def test_no_network_blocks_loopback_without_marker() -> None:
    """Unmarked tests cannot reach a local server either."""
    with socket.create_server(("127.0.0.1", 0)) as server:
        with pytest.raises(RuntimeError, match="Network access is disabled"):
            socket.create_connection(server.getsockname()[:2], timeout=1)


@pytest.mark.loopback
def test_loopback_marker_allows_local_server_only() -> None:
    """`loopback` tests reach `127.0.0.1`, but outside hosts stay blocked."""
    with socket.create_server(("127.0.0.1", 0)) as server:
        with socket.create_connection(server.getsockname()[:2], timeout=1):
            pass
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            assert sock.connect_ex(server.getsockname()[:2]) == 0
        finally:
            sock.close()

    with pytest.raises(RuntimeError, match="Network access is disabled"):
        socket.create_connection(("93.184.216.34", 80), timeout=0.1)


def test_sentinel_autouse_redirects_aldwin_home_and_user_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-02: Autouse fixture redirects `ALDWIN_HOME` and `HOME` without args."""
    current_home = home_dir()
    assert current_home.name == "home"
    assert current_home.is_dir()
    assert (current_home / "config.yaml").is_file()
    assert (current_home / "persona.md").is_file()
    assert "OPENAI_API_KEY" not in os.environ
    assert "TAVILY_API_KEY" not in os.environ

    # Even if a test deletes ALDWIN_HOME, Path.home() is isolated inside tmp_path.
    monkeypatch.delenv("ALDWIN_HOME", raising=False)
    fallback_home = home_dir()
    assert fallback_home == (Path.home() / ".aldwin").resolve()
    assert fallback_home.parent.name == "user"
    assert fallback_home.parent != Path("/Users")


async def test_fake_model_builders_and_agent_session_fixture(
    fake_model: FakeModel,
    agent_session: AgentSession,
    aldwin_home: Path,
) -> None:
    """TC-13: Top-level `FakeModel` builders and `agent_session` drive a core turn."""
    split_turn = text("HelloWorld", deltas=5)
    assert split_turn.chunks == ["He", "ll", "oW", "or", "ld"]
    assert text("Single", deltas=1).chunks == ["Single"]

    with pytest.raises(ValueError, match="Cannot split"):
        text("hi", deltas=5)
    with pytest.raises(ValueError, match="Cannot split"):
        text("hello", deltas=0)

    err_turn = raise_(ValueError("boom"))
    assert isinstance(err_turn.exc, ValueError)

    hang_turn = hang(prefix_chunks=["wait"])
    assert hang_turn.hang_forever is True
    assert hang_turn.chunks == ["wait"]

    fake_model.add_turns(
        tool_call("remember", {"text": "Fola prefers tea over coffee."}),
        final("Saved to your notes."),
    )

    final_ev: FinalEvent | None = None
    async for ev in agent_session.run("Remember I prefer tea.", stream=True):
        if isinstance(ev, FinalEvent):
            final_ev = ev

    assert final_ev is not None
    assert final_ev.status == "completed"
    assert final_ev.answer == "Saved to your notes."
    assert len(fake_model.calls) == 2
    assert fake_model.received_messages[0].role == "system"
    assert "<memory>" in fake_model.received_messages[0].content

    note_files = list((aldwin_home / "memory" / "notes").glob("*.md"))
    assert len(note_files) == 1
    assert "Fola prefers tea over coffee." in note_files[0].read_text(encoding="utf-8")


async def test_fake_host_records_requests_and_yields_events(
    fake_host: FakeHost,
) -> None:
    """TC-14: `FakeHost` (`fake_host` fixture) yields events and records requests."""
    fake_host.queue_turn(
        [
            TextEvent(re="r1", delta="Checking"),
            ApprovalEvent(
                re="r1",
                approval_id="ap_42",
                summary="Run this command?",
                detail="df -h",
            ),
            DoneEvent(re="r1", reason="answered"),
        ]
    )

    await fake_host.connect()
    await fake_host.history(limit=20)
    turn_events = await auto_approve(
        fake_host,  # type: ignore[arg-type]
        fake_host.turn("check disk"),
        answer="yes",
    )
    assert len(turn_events) == 3
    await fake_host.stop()

    ok_model = await fake_host.set_model("gpt-4.1")
    assert isinstance(ok_model, DoneEvent)
    bad_model = await fake_host.set_model("not-a-model")
    assert isinstance(bad_model, ErrorEvent)

    await fake_host.new_conversation()
    assert await collect(fake_host.turn("default turn")) == [
        DoneEvent(re="req_turn", reason="answered")
    ]

    assert fake_host.requests == [
        ("connect",),
        ("history", 20),
        ("turn", "check disk", []),
        ("approve", "ap_42", "yes"),
        ("stop",),
        ("set_model", "gpt-4.1"),
        ("set_model", "not-a-model"),
        ("new_conversation",),
        ("turn", "default turn", []),
    ]
