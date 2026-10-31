"""`aldwin connectors …` shell commands (`FR-27`, `TC-14`)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from typer.testing import CliRunner

from aldwin.cli.main import app
from aldwin.client import connectors_login_command, connectors_logout_command
from aldwin.core.connectors.config import load_connectors
from aldwin.core.connectors.credentials import FileCredentialStore
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


# --- sign-in ------------------------------------------------------------------


def test_add_with_client_id_saves_an_oauth_entry_unchecked(
    fake_probe: list[Any], aldwin_home: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "connectors",
            "add",
            "notion",
            "--url",
            "https://mcp.notion.example/mcp",
            "--client-id",
            "pre-id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Notion added. Sign in with: aldwin connectors login notion" in result.output
    assert fake_probe == []
    config = load_connectors(aldwin_home).entries["notion"]
    assert config.auth == "oauth"
    assert config.oauth is not None and config.oauth.client_id == "pre-id"


def test_login_and_logout_refuse_token_connectors(
    fake_probe: list[Any], aldwin_home: Path
) -> None:
    runner.invoke(
        app, ["connectors", "add", "plain", "--url", "https://plain.example/mcp"]
    )
    for command in ("login", "logout"):
        result = runner.invoke(app, ["connectors", command, "plain"])
        assert result.exit_code == 1
        assert "Plain doesn't use browser sign-in." in result.output
    missing = runner.invoke(app, ["connectors", "login", "nope"])
    assert missing.exit_code == 1
    assert "No connector named nope." in missing.output


async def _approve(url: str) -> str:
    async with httpx2.AsyncClient(follow_redirects=False) as http:
        return str((await http.get(url)).headers["location"])


def _save_oauth(home: Path, url: str) -> None:
    from aldwin.core.connectors.config import ConnectorConfig, save_connectors

    save_connectors(
        home, {"fixture": ConnectorConfig(name="fixture", url=url, auth="oauth")}
    )


@pytest.mark.loopback
async def test_login_with_paste_signs_in(oauth_server: Any, aldwin_home: Path) -> None:
    _save_oauth(aldwin_home, oauth_server.mcp_url)
    shown: list[str] = []
    pastes = iter(["not an address"])

    async def read_line(_prompt: str) -> str:
        bad = next(pastes, None)
        if bad is not None:
            return bad
        [url] = [line for line in shown if line.startswith(oauth_server.issuer)]
        return await _approve(url)

    row = await connectors_login_command(
        "fixture",
        paste=True,
        show=shown.append,
        read_line=read_line,
        open_browser=lambda _url: pytest.fail("no browser in paste mode"),
    )
    assert row["tools_count"] == 5
    assert any("paste the address the browser ends on" in line for line in shown)
    assert any("no sign-in code" in line for line in shown)
    assert FileCredentialStore(aldwin_home).get("fixture") is not None

    connectors_logout_command("fixture")
    assert FileCredentialStore(aldwin_home).get("fixture") is None


@pytest.mark.loopback
async def test_login_through_the_browser(oauth_server: Any, aldwin_home: Path) -> None:
    _save_oauth(aldwin_home, oauth_server.mcp_url)
    trips: list[asyncio.Task[None]] = []

    async def browser_trip(url: str) -> None:
        back = await _approve(url)
        async with httpx2.AsyncClient() as http:
            await http.get(back)

    def open_browser(url: str) -> bool:
        trips.append(asyncio.create_task(browser_trip(url)))
        return True

    async def no_paste(_prompt: str) -> str:
        raise AssertionError("the browser came back; nothing to paste")

    shown: list[str] = []
    row = await connectors_login_command(
        "fixture",
        paste=False,
        show=shown.append,
        read_line=no_paste,
        open_browser=open_browser,
    )
    await asyncio.gather(*trips)
    assert row["tools_count"] == 5
    assert "Waiting for you to finish in the browser…" in shown


def test_logout_deletes_the_sign_in(aldwin_home: Path) -> None:
    _save_oauth(aldwin_home, "https://mcp.notion.example/mcp")
    store = FileCredentialStore(aldwin_home)
    store.set("fixture", {"v": 1, "kind": "oauth"})
    result = runner.invoke(app, ["connectors", "logout", "fixture"])
    assert result.exit_code == 0, result.output
    assert "fixture signed out." in result.output
    assert store.get("fixture") is None
