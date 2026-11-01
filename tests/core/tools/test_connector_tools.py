"""`find_connector_tools`: what the model is told, with the host faked."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from agentling.tools import ToolCallError

from aldwin.core.connectors.index import IndexedTool, ToolIndex
from aldwin.core.tools.connector_tools import (
    FindConnectorToolsTool,
    LoadResult,
    build_find_connector_tools_tool,
)


def _tool(connector: str, name: str, description: str, *, auto: bool) -> IndexedTool:
    return IndexedTool(
        connector=connector,
        title=name.replace("_", " ").title(),
        name=name,
        model_name=f"mcp__{connector}__{name}",
        description=description,
        auto=auto,
    )


READ = _tool("alpha", "read_thing", "Read the thing", auto=True)
WRITE = _tool("alpha", "write_thing", "Write the thing\nwith care", auto=False)
PAGE = _tool("beta", "get_page", "Fetch a page", auto=True)


class Host:
    """Records loads; by default everything asked for loads."""

    def __init__(self) -> None:
        self.index = ToolIndex()
        self.index.replace("alpha", [READ, WRITE])
        self.index.replace("beta", [PAGE])
        self.loads: list[list[str]] = []
        self.result: LoadResult | None = None

    def load(self, tools: Sequence[IndexedTool]) -> LoadResult:
        self.loads.append([t.model_name for t in tools])
        return self.result or LoadResult(loaded=list(tools))

    def tool(self) -> FindConnectorToolsTool:
        built = build_find_connector_tools_tool(index=self.index, load=self.load)
        assert isinstance(built, FindConnectorToolsTool)
        return built


async def test_query_loads_matches_and_says_how_they_run() -> None:
    host = Host()
    out = await host.tool().call({"query": "thing"})
    assert out == (
        "mcp__alpha__read_thing: Read the thing (runs automatically)\n"
        "mcp__alpha__write_thing: Write the thing with care (asks the user first)\n"
        "Loaded 2 tools. You can call them now."
    )
    assert host.loads == [["mcp__alpha__read_thing", "mcp__alpha__write_thing"]]


async def test_query_scoped_to_a_connector() -> None:
    host = Host()
    out = await host.tool().call({"query": "page", "connector": "beta"})
    assert out.startswith("mcp__beta__get_page: Fetch a page")
    out = await host.tool().call({"query": "thing", "connector": "beta"})
    assert out == 'No connector tool matches "thing". Connected apps: alpha, beta.'


async def test_names_load_known_and_report_unknown() -> None:
    """`TC-07`."""
    host = Host()
    out = await host.tool().call({"connector": "beta", "names": ["get_page", "nope"]})
    assert out == (
        "No tool named nope on beta.\n"
        "mcp__beta__get_page: Fetch a page (runs automatically)\n"
        "Loaded 1 tools. You can call them now."
    )
    assert host.loads == [["mcp__beta__get_page"]]


async def test_only_unknown_names_loads_nothing() -> None:
    host = Host()
    out = await host.tool().call({"connector": "alpha", "names": ["nope"]})
    assert out == "No tool named nope on alpha."
    assert host.loads == []


async def test_no_match_and_unknown_connector() -> None:
    host = Host()
    out = await host.tool().call({"query": "tweet"})
    assert out == 'No connector tool matches "tweet". Connected apps: alpha, beta.'
    out = await host.tool().call({"query": "thing", "connector": "gamma"})
    assert out == "No connector named gamma. Connected apps: alpha, beta."
    assert host.loads == []


async def test_no_connectors_at_all() -> None:
    host = Host()
    host.index.drop("alpha")
    host.index.drop("beta")
    out = await host.tool().call({"query": "thing"})
    assert out == 'No connector tool matches "thing". Connected apps: none.'


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({}, "Give a query, or a connector and the names of the tools you want."),
        ({"query": "  "}, "Give a query, or a connector and the names of the tools"),
        ({"names": ["read_thing"]}, "names needs connector."),
        ({"connector": "alpha", "names": ["x"] * 21}, "At most 20 names."),
    ],
)
async def test_validation(args: dict[str, object], message: str) -> None:
    """`TC-08`."""
    host = Host()
    with pytest.raises(ToolCallError, match=message):
        await host.tool().call(args)
    assert host.loads == []


async def test_reports_skipped_and_evicted() -> None:
    host = Host()
    host.result = LoadResult(
        loaded=[READ], evicted=["mcp__beta__get_page"], skipped=[WRITE]
    )
    out = await host.tool().call({"query": "thing"})
    assert out == (
        "mcp__alpha__read_thing: Read the thing (runs automatically)\n"
        "mcp__alpha__write_thing: not loaded (over the limit of loaded tools)\n"
        "Unloaded to make room: mcp__beta__get_page.\n"
        "Loaded 1 tools. You can call them now."
    )


async def test_description_is_sanitised_and_cut() -> None:
    host = Host()
    long = "x" * 200 + "‮tail"
    host.index.replace("alpha", [_tool("alpha", "big", long, auto=True)])
    out = await host.tool().call({"connector": "alpha", "names": ["big"]})
    first = out.splitlines()[0]
    assert first == f"mcp__alpha__big: {'x' * 160} (runs automatically)"
    assert "‮" not in out


async def test_empty_description_falls_back_to_title() -> None:
    host = Host()
    host.index.replace("alpha", [_tool("alpha", "read_thing", "", auto=True)])
    out = await host.tool().call({"connector": "alpha", "names": ["read_thing"]})
    assert out.startswith("mcp__alpha__read_thing: Read Thing (runs automatically)")
