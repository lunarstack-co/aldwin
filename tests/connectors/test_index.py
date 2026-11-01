"""The connector tool index: what the model can find, and in what order."""

from __future__ import annotations

import time

from aldwin.core.connectors.index import IndexedTool, ToolIndex, tokens


def _tool(
    name: str,
    *,
    connector: str = "alpha",
    title: str = "",
    description: str = "",
    auto: bool = False,
) -> IndexedTool:
    return IndexedTool(
        connector=connector,
        title=title or name,
        name=name,
        model_name=f"mcp__{connector}__{name}",
        description=description,
        auto=auto,
    )


FIXTURE = [
    _tool("read_thing", auto=True, description="Read the thing"),
    _tool("write_thing", description="Write the thing"),
    _tool("echo_nullable", description="Echo an optional value"),
]


def test_tokens_split_snake_and_camel_case() -> None:
    assert tokens("read_thing") == ["read", "thing"]
    assert tokens("listIssues") == ["list", "issues"]
    assert tokens("HTTPServer v2") == ["http", "server", "v", "2"]
    assert tokens("") == []


def test_search_by_name_token() -> None:
    index = ToolIndex()
    index.replace("alpha", FIXTURE)
    found = index.search("read")
    assert [t.name for t in found] == ["read_thing"]


def test_search_by_description_and_camel_case() -> None:
    """A word in the name and the description scores higher."""
    index = ToolIndex()
    index.replace(
        "linear",
        [
            _tool("listIssues", description="List issues in a project"),
            _tool("getProject", description="Fetch one project"),
            _tool("createComment", description="Comment on an issue"),
        ],
    )
    assert [t.name for t in index.search("issues")] == ["listIssues"]
    # "issue" is a prefix of "issues": name plus description outscores description.
    assert [t.name for t in index.search("issue")] == ["listIssues", "createComment"]


def test_search_no_match_and_limit() -> None:
    index = ToolIndex()
    index.replace("alpha", [_tool(f"thing_{i:02d}") for i in range(12)])
    assert len(index.search("thing", limit=5)) == 5
    assert index.search("zzz") == []
    assert index.search("") == []
    assert index.search("   ") == []


def test_search_prefix_needs_three_characters() -> None:
    index = ToolIndex()
    index.replace("alpha", FIXTURE)
    assert [t.name for t in index.search("rea")] == ["read_thing"]
    assert index.search("re") == []


def test_search_ties_follow_connector_order_then_name() -> None:
    index = ToolIndex()
    index.replace("beta", [_tool("thing_b", connector="beta")])
    index.replace("alpha", [_tool("thing_a"), _tool("thing_0")])
    assert [t.model_name for t in index.search("thing")] == [
        "mcp__beta__thing_b",
        "mcp__alpha__thing_0",
        "mcp__alpha__thing_a",
    ]


def test_search_scoped_to_one_connector() -> None:
    index = ToolIndex()
    index.replace("alpha", FIXTURE)
    index.replace("beta", [_tool("read_thing", connector="beta")])
    found = index.search("read", connector="beta")
    assert [t.model_name for t in found] == ["mcp__beta__read_thing"]
    assert index.search("read", connector="nope") == []


def test_long_query_is_cut() -> None:
    index = ToolIndex()
    index.replace("alpha", FIXTURE)
    query = "x" * 200 + " read"
    assert index.search(query) == []


def test_replace_drop_get_and_names() -> None:
    index = ToolIndex()
    index.replace("alpha", FIXTURE)
    assert index.connectors() == ["alpha"]
    assert index.names("alpha") == ["read_thing", "write_thing", "echo_nullable"]
    assert index.get("alpha", "read_thing") is FIXTURE[0]
    assert index.get("alpha", "mcp__alpha__read_thing") is FIXTURE[0]
    assert index.get("beta", "mcp__alpha__read_thing") is None
    assert index.get("alpha", "nope") is None
    assert index.get("nope", "read_thing") is None

    index.replace("alpha", FIXTURE[:1])
    assert index.names("alpha") == ["read_thing"]

    index.drop("alpha")
    index.drop("alpha")
    assert index.names("alpha") == []
    assert index.connectors() == []


def test_search_a_thousand_tools_is_fast() -> None:
    index = ToolIndex()
    for c in range(10):
        index.replace(
            f"c{c}",
            [
                _tool(
                    f"tool_{i}_{c}",
                    connector=f"c{c}",
                    description=f"Does thing number {i} for connector {c}",
                )
                for i in range(100)
            ],
        )
    started = time.perf_counter()
    found = index.search("thing number 7")
    assert len(found) == 5
    assert time.perf_counter() - started < 0.05
