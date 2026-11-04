"""Tests for `builtin_tools()`, `list_builtin_tools()`, and host integration."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient, get_builtin_tools
from aldwin.core.agent import build_agent
from aldwin.core.home import ensure_home
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.core.tools import (
    BUILTIN_TOOL_NAMES,
    ToolInfo,
    ToolOutput,
    builtin_tools,
    list_builtin_tools,
    summarize_tool_result,
)
from aldwin.core.tools.run_command import (
    build_run_command_tool,
    check_command_allowed,
    scrub_env,
)
from aldwin.core.tools.web_fetch import build_web_fetch_tool
from aldwin.core.tools.web_search import (
    SearchResult,
    build_web_search_tool,
)
from aldwin.host import AldwinHost, AskFn, InProcessTransport
from aldwin.protocol import (
    ApprovalEvent,
    DoneEvent,
    Event,
    ToolResultEvent,
)


async def _dummy_ask(_summary: str, _detail: str = "") -> bool:
    return True


class _FastSearchProvider:
    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"Result {i} for {query}",
                url=f"https://example.com/{i}",
                snippet=f"Snippet {i}",
            )
            for i in range(1, min(limit, 3) + 1)
        ]


def test_builtin_tools_returns_exact_six_names(aldwin_home: Path) -> None:
    """TC-14: `builtin_tools()` returns the exact six tool names, no more."""
    ensure_home(aldwin_home)
    tools = builtin_tools(_dummy_ask, home=aldwin_home, secrets=SecretsConfig())

    names = tuple(t.name for t in tools)
    assert names == (
        "web_search",
        "web_fetch",
        "run_command",
        "remember",
        "recall",
        "forget",
        "set_persona",
    )
    assert names == BUILTIN_TOOL_NAMES

    for tool_obj in tools:
        defn = tool_obj.definition
        assert defn.name == tool_obj.name
        assert defn.description
        assert isinstance(defn.parameters, dict)


def test_list_builtin_tools_metadata() -> None:
    """TC-24: `list_builtin_tools()` and `get_builtin_tools()` return metadata."""
    items = list_builtin_tools()
    client_items = get_builtin_tools()
    assert items == client_items
    assert [item.name for item in items] == [
        *BUILTIN_TOOL_NAMES,
        "list_dir",
        "glob",
        "grep",
        "read_file",
        "edit_file",
        "write_file",
        "read_document",
        "download_file",
        "todo",
        "calculate",
        "create_skill",
    ]

    by_name: dict[str, ToolInfo] = {item.name: item for item in items}
    for item in items:
        assert item.description
        assert "\n" not in item.description

    assert by_name["run_command"].requires_approval is True
    assert by_name["set_persona"].requires_approval is True
    assert by_name["web_search"].requires_approval is False
    assert by_name["web_fetch"].requires_approval is False
    assert by_name["remember"].requires_approval is False
    assert by_name["recall"].requires_approval is False
    assert by_name["forget"].requires_approval is True
    assert by_name["list_dir"].requires_approval is False
    assert by_name["glob"].requires_approval is False
    assert by_name["grep"].requires_approval is False
    assert by_name["read_file"].requires_approval is False
    assert by_name["edit_file"].requires_approval is True
    assert by_name["write_file"].requires_approval is True
    assert by_name["read_document"].requires_approval is False
    assert by_name["download_file"].requires_approval is True
    assert by_name["todo"].requires_approval is False
    assert by_name["calculate"].requires_approval is False


def test_tools_approval_notes_match_policy_registry() -> None:
    """Each `/tools` approval note describes the tool's tier in `policy.REGISTRY`."""
    import aldwin.core.tools as tools_mod
    from aldwin.core.policy import REGISTRY

    for item in list_builtin_tools():
        policy = REGISTRY[item.name]
        if callable(policy.tier):
            expected = tools_mod.APPROVAL_NOTE_REMEMBER
        elif policy.tier == "ask":
            expected = tools_mod.APPROVAL_NOTE_ASK
        elif policy.tier == "diff" and policy.never_grant is None:
            expected = tools_mod.APPROVAL_NOTE_DIFF_GRANTABLE
        elif policy.tier == "diff":
            expected = tools_mod.APPROVAL_NOTE_DIFF
        else:
            expected = ""
        assert item.approval_note == expected, item.name
        assert item.requires_approval is (policy.tier in ("ask", "diff")), item.name


def test_tool_output_and_summarize_tool_result_contract() -> None:
    """`ToolOutput` preserves `.summary` and `.ok` across `str()` and `+`."""
    out = ToolOutput("full body text", summary="3 results", ok=True)
    assert isinstance(out, str)
    assert str(out) is out
    assert out.summary == "3 results"
    assert out.ok is True

    nudged = out + " Note: loop nudge"
    assert isinstance(nudged, ToolOutput)
    assert nudged.summary == "3 results"
    assert nudged.ok is True

    assert summarize_tool_result("web_search", out) == (True, "3 results")
    assert summarize_tool_result("web_search", out, is_error=True) == (
        False,
        "3 results",
    )
    assert summarize_tool_result("custom", "first line\nsecond line") == (
        True,
        "first line",
    )
    assert summarize_tool_result("custom", "   \n  ") == (True, "")

    import aldwin.core.tools as tools_mod

    with pytest.raises(AttributeError):
        _ = tools_mod.nonexistent_tool_attr


async def test_host_emits_tool_summaries_and_preserves_20k_fetch_in_memory(
    aldwin_home: Path,
    tmp_path: Path,
) -> None:
    """TC-25: `AldwinHost` maps `ToolOutput` summaries and keeps 20k fetch in memory."""
    ensure_home(aldwin_home)

    big_text = "B" * 25_000

    def fetch_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            text=big_text,
            request=request,
        )

    async def public_resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    fake_model = FakeModel(
        turns=[
            FakeTurn.tool_call(
                "web_search",
                {"query": "Python 3.14", "limit": 3},
                call_id="c_search",
            ),
            FakeTurn.tool_call(
                "web_fetch",
                {"url": "https://docs.python.org/3.14/"},
                call_id="c_fetch",
            ),
            FakeTurn.tool_call(
                "run_command",
                {"command": "echo disk_ok"},
                call_id="c_cmd_ok",
            ),
            FakeTurn.tool_call(
                "run_command",
                {"command": "sudo rm -rf /"},
                call_id="c_cmd_sudo",
            ),
            FakeTurn.text(["All checks done."]),
        ]
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(fetch_handler)
    ) as http_client:

        def builder(model_name: str, ask: AskFn) -> Any:
            tools = [
                build_web_search_tool(
                    home=aldwin_home,
                    provider=_FastSearchProvider(),
                ),
                build_web_fetch_tool(
                    client=http_client,
                    resolver=public_resolver,
                ),
                build_run_command_tool(ask=ask, cwd=tmp_path),
            ]
            return build_agent(
                AldwinConfig(model=model_name),
                SecretsConfig(),
                ask,
                home=aldwin_home,
                model=fake_model,
                tools=tools,
            )

        transport = InProcessTransport()
        host = AldwinHost(transport, agent_builder=builder)
        await host.start()
        client = AldwinClient(transport.connect())

        try:
            await client.connect()
            events: list[Event] = []
            async for ev in client.turn("Run all tools"):
                events.append(ev)
                if isinstance(ev, ApprovalEvent):
                    await client.approve(ev.approval_id, "yes")

            results_by_id = {
                ev.call_id: ev for ev in events if isinstance(ev, ToolResultEvent)
            }
            assert results_by_id["c_search"].ok is True
            assert results_by_id["c_search"].summary == "3 results"

            assert results_by_id["c_fetch"].ok is True
            assert results_by_id["c_fetch"].summary == "20k chars"

            assert results_by_id["c_cmd_ok"].ok is True
            assert results_by_id["c_cmd_ok"].summary == "exit 0"

            assert results_by_id["c_cmd_sudo"].ok is False
            assert results_by_id["c_cmd_sudo"].summary == "sudo is not allowed"

            # Full 20,000-char untrusted payload stays in session memory.
            dumped = host.stored_session.memory.dump_json()
            assert ("B" * 20_000) in dumped
            assert "characters omitted" not in dumped
        finally:
            await client.close()
            await host.stop_host()


async def test_esc_during_running_command_and_approval_via_host(
    aldwin_home: Path,
    tmp_path: Path,
) -> None:
    """TC-13 & TC-26: `Esc` during running command or approval stops in < 1s."""
    ensure_home(aldwin_home)
    pid_file = tmp_path / "host_sleep.pid"
    never_created = tmp_path / "never_created.txt"

    fake_model = FakeModel(
        turns=[
            FakeTurn.tool_call(
                "run_command",
                {"command": f"sleep 30 & echo $! > {pid_file}; wait"},
                call_id="c_sleep",
            ),
            FakeTurn.tool_call(
                "run_command",
                {"command": f"touch {never_created}"},
                call_id="c_declined_by_esc",
            ),
        ]
    )

    def builder(model_name: str, ask: AskFn) -> Any:
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
            tools=[build_run_command_tool(ask=ask, cwd=tmp_path)],
        )

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder)
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()

        # 1. TC-13: Approve command, wait for child PID, then press Esc.
        turn1_events: list[Event] = []

        async def run_turn_1() -> None:
            async for ev in client.turn("sleep for 30s"):
                turn1_events.append(ev)
                if isinstance(ev, ApprovalEvent):
                    await client.approve(ev.approval_id, "yes")

        t1 = asyncio.create_task(run_turn_1())
        for _ in range(50):
            if pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip():
                break
            await asyncio.sleep(0.02)

        assert pid_file.is_file()
        child_pid = int(pid_file.read_text(encoding="utf-8").strip())

        t0 = time.monotonic()
        stop_res = await client.stop()
        await asyncio.wait_for(t1, timeout=1.0)
        elapsed_1 = time.monotonic() - t0

        assert elapsed_1 < 1.0
        assert isinstance(stop_res, DoneEvent) and stop_res.reason == "ok"
        assert isinstance(turn1_events[-1], DoneEvent)
        assert turn1_events[-1].reason == "stopped"
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)

        # 2. TC-26: Press Esc while waiting on the approval prompt.
        turn2_events: list[Event] = []
        t0_approval = 0.0
        async for ev in client.turn("touch file"):
            turn2_events.append(ev)
            if isinstance(ev, ApprovalEvent):
                t0_approval = time.monotonic()
                await client.stop()

        elapsed_2 = time.monotonic() - t0_approval
        assert elapsed_2 < 1.0
        assert isinstance(turn2_events[-1], DoneEvent)
        assert turn2_events[-1].reason == "stopped"
        assert not never_created.exists()
    finally:
        await client.close()
        await host.stop_host()


async def test_tool_wrapper_overhead_under_50ms(aldwin_home: Path) -> None:
    """TC-27: Tool wrapper overhead (excluding network/subprocess wait) is < 50 ms."""
    ensure_home(aldwin_home)

    search_tool = build_web_search_tool(
        home=aldwin_home,
        provider=_FastSearchProvider(),
    )
    t0 = time.perf_counter()
    await search_tool.call({"query": "fast check", "limit": 3})
    search_ms = (time.perf_counter() - t0) * 1000
    assert search_ms < 50.0

    def fast_html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            text="Fast local body.",
            request=request,
        )

    async def fast_resolver(_h: str, _p: int) -> list[str]:
        return ["93.184.216.34"]

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(fast_html)
    ) as http_client:
        fetch_tool = build_web_fetch_tool(
            client=http_client,
            resolver=fast_resolver,
        )
        t1 = time.perf_counter()
        await fetch_tool.call({"url": "https://example.com/fast"})
        fetch_ms = (time.perf_counter() - t1) * 1000
        assert fetch_ms < 50.0

    t2 = time.perf_counter()
    assert check_command_allowed("df -h | head -n 5") is None
    scrub_env({"OPENAI_API_KEY": "sk-123", "PATH": "/usr/bin"})
    cmd_overhead_ms = (time.perf_counter() - t2) * 1000
    assert cmd_overhead_ms < 50.0
