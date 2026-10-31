"""`aldwin connectors …` shell commands (`FR-27`, `TC-14`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from aldwin.cli.main import app
from aldwin.core.connectors.config import load_connectors
from aldwin.core.connectors.errors import ConnectorUnreachable
from aldwin.core.connectors.mcp_tool import RemoteToolSpec
from aldwin.host.connectors import ConnectorAdmin

runner = CliRunner()


@pytest.fixture
def fake_probe(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Replace the network probe; returns the configs it was asked about."""
    seen: list[Any] = []

    async def probe(self: ConnectorAdmin, config: Any, token: Any = None) -> Any:
        seen.append(config)
        if "down" in config.url:
            raise ConnectorUnreachable("down.example", "connection refused")
        return [
            RemoteToolSpec(name="read_thing", read_only_hint=True),
            RemoteToolSpec(name="write_thing"),
        ]

    monkeypatch.setattr(ConnectorAdmin, "probe", probe)
    return seen


def _json_list() -> list[dict[str, Any]]:
    result = runner.invoke(app, ["connectors", "list", "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_list_json_empty_then_two(fake_probe: list[Any], aldwin_home: Path) -> None:
    """`TC-14`."""
    assert _json_list() == []
    for name in ("context7", "github"):
        args = ["connectors", "add", name, "--url", f"https://{name}.example/mcp"]
        if name == "github":
            args += ["--token-env", "GITHUB_TOKEN"]
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    rows = _json_list()
    assert [r["name"] for r in rows] == ["context7", "github"]
    assert rows[1]["auth"] == "bearer" and rows[1]["token_env"] == "GITHUB_TOKEN"
    assert "token" not in json.dumps(rows).replace("token_env", "")


def test_add_reports_tools(fake_probe: list[Any]) -> None:
    result = runner.invoke(
        app, ["connectors", "add", "ctx", "--url", "https://ctx.example/mcp"]
    )
    assert result.exit_code == 0
    assert "Ctx added: 2 tools, 1 read (run automatically), 1 write (ask first)." in (
        result.output
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ["add", "x", "--url", "http://example.com"],
            "Connector URL must use https:// (got http://example.com)",
        ),
        (
            ["add", "x", "--url", "https://down.example/mcp"],
            "Couldn't reach down.example: connection refused.",
        ),
        (["remove", "nope"], "No connector named nope."),
        (["enable", "nope"], "No connector named nope."),
    ],
)
def test_user_errors_exit_1_and_save_nothing(
    fake_probe: list[Any], aldwin_home: Path, args: list[str], message: str
) -> None:
    result = runner.invoke(app, ["connectors", *args])
    assert result.exit_code == 1
    assert message in result.output
    assert load_connectors(aldwin_home).entries == {}


def test_duplicate_name_exits_1(fake_probe: list[Any]) -> None:
    args = ["connectors", "add", "ctx", "--url", "https://ctx.example/mcp"]
    assert runner.invoke(app, args).exit_code == 0
    again = runner.invoke(app, args)
    assert again.exit_code == 1
    assert "A connector named ctx already exists" in again.output


def test_disable_enable_remove(fake_probe: list[Any], aldwin_home: Path) -> None:
    runner.invoke(app, ["connectors", "add", "ctx", "--url", "https://ctx.example/mcp"])
    assert runner.invoke(app, ["connectors", "disable", "ctx"]).exit_code == 0
    assert load_connectors(aldwin_home).entries["ctx"].enabled is False
    assert runner.invoke(app, ["connectors", "enable", "ctx"]).exit_code == 0
    assert load_connectors(aldwin_home).entries["ctx"].enabled is True
    plain = runner.invoke(app, ["connectors", "list"])
    assert "ctx  https://ctx.example/mcp  (on)" in plain.output
    assert runner.invoke(app, ["connectors", "remove", "ctx"]).exit_code == 0
    assert load_connectors(aldwin_home).entries == {}


def test_unexpected_error_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("aldwin.client.connectors_summary", boom)
    result = runner.invoke(app, ["connectors", "list"])
    assert result.exit_code == 2
    assert "disk on fire" in result.output
