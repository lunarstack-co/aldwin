"""`aldwin connectors add --command` and `enable`: local servers ask first."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

import pytest
from connectors._proc import fixture_argv
from typer.testing import CliRunner

from aldwin.cli import connectors_cmd
from aldwin.cli.main import app
from aldwin.core.connectors import stdio
from aldwin.core.connectors.config import (
    ConnectorConfig,
    connectors_path,
    load_connectors,
    save_connectors,
)
from aldwin.core.connectors.local import launch_digest

runner = CliRunner()
FIXTURE = shlex.join(fixture_argv())


@pytest.fixture(autouse=True)
def no_leaks() -> Any:
    try:
        yield
    finally:
        leaked = set(stdio._LIVE_PGIDS)
        stdio.kill_live_groups()
        assert not leaked, f"process groups left running: {sorted(leaked)}"


@pytest.fixture
def tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connectors_cmd, "_stdin_is_tty", lambda: True)


def _log_lines(home: Path) -> list[dict[str, Any]]:
    path = home / "logs" / "approvals.jsonl"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_add_local_without_tty_or_yes_exits_1(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[Any] = []
    monkeypatch.setattr(
        "aldwin.client.connectors_add_local_command",
        lambda *a, **k: called.append(a),
    )
    result = runner.invoke(app, ["connectors", "add", "x", "--command", "python -V"])
    assert result.exit_code == 1
    assert result.stderr.strip() == connectors_cmd.ADD_NEEDS_YES
    assert called == []
    assert not connectors_path(aldwin_home).exists()


def test_add_local_with_yes_saves_and_logs_reason(aldwin_home: Path) -> None:
    result = runner.invoke(
        app, ["connectors", "add", "fixture", "--command", FIXTURE, "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "Fixture added: 4 tools" in result.stdout
    saved = load_connectors(aldwin_home).entries["fixture"]
    assert saved.approved_digest == launch_digest(saved)
    line = _log_lines(aldwin_home)[-1]
    assert (line["answer"], line["reason"], line["tool"]) == (
        "yes",
        "approved with --yes",
        "connectors.add",
    )
    assert not stdio._LIVE_PGIDS


def test_add_local_tty_enter_means_no(aldwin_home: Path, tty: None) -> None:
    result = runner.invoke(
        app, ["connectors", "add", "fixture", "--command", FIXTURE], input="\n"
    )
    assert result.exit_code == 1
    for prefix in ("Command:", "Folder:", "Environment:"):
        assert prefix in result.stdout
    assert "runs a program on this computer" in result.stdout
    assert "Fixture was not approved, so nothing was saved." in result.stderr
    assert not connectors_path(aldwin_home).exists()

    result = runner.invoke(
        app, ["connectors", "add", "fixture", "--command", FIXTURE], input="y\n"
    )
    assert result.exit_code == 0, result.output
    assert "fixture" in load_connectors(aldwin_home).entries


def test_enable_local_needs_yes_without_tty(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = fixture_argv()
    imported = ConnectorConfig(
        name="github",
        command=argv[0],
        args=argv[1:],
        enabled=False,
        note="review and enable",
    )
    save_connectors(aldwin_home, {"github": imported})
    before = connectors_path(aldwin_home).read_bytes()
    called: list[Any] = []
    with monkeypatch.context() as patch:
        patch.setattr(
            "aldwin.client.connectors_enable_command",
            lambda *a, **k: called.append(a),
        )
        result = runner.invoke(app, ["connectors", "enable", "github"])
    assert result.exit_code == 1
    assert result.stderr.strip() == connectors_cmd.ENABLE_NEEDS_YES
    assert called == [] and connectors_path(aldwin_home).read_bytes() == before

    result = runner.invoke(app, ["connectors", "enable", "github", "--yes"])
    assert result.exit_code == 0, result.output
    saved = load_connectors(aldwin_home).entries["github"]
    assert saved.enabled and saved.approved_digest == launch_digest(saved)
    assert _log_lines(aldwin_home)[-1]["tool"] == "connectors.enable"


def test_enable_remote_needs_no_approval(aldwin_home: Path) -> None:
    save_connectors(
        aldwin_home,
        {
            "web": ConnectorConfig(
                name="web", url="https://web.example/mcp", enabled=False
            )
        },
    )
    result = runner.invoke(app, ["connectors", "enable", "web"])
    assert result.exit_code == 0, result.output
    assert load_connectors(aldwin_home).entries["web"].enabled


def test_add_needs_exactly_one_of_url_and_command(aldwin_home: Path) -> None:
    result = runner.invoke(app, ["connectors", "add", "x"])
    assert result.exit_code == 1
    assert "Give either --url or --command." in result.stderr
    result = runner.invoke(
        app,
        ["connectors", "add", "x", "--url", "https://a.example/mcp", "--command", "a"],
    )
    assert result.exit_code == 1


def test_list_shows_the_command_of_a_local_entry(aldwin_home: Path) -> None:
    save_connectors(
        aldwin_home,
        {"pkg": ConnectorConfig(name="pkg", command="npx", args=["-y", "pkg@1"])},
    )
    result = runner.invoke(app, ["connectors", "list"])
    assert "pkg  npx -y pkg@1  (on)" in result.stdout


IMPORTS = Path(__file__).resolve().parents[1] / "fixtures" / "import"


def _table_rows(stdout: str) -> list[list[str]]:
    return [line.split() for line in stdout.splitlines() if line.strip()]


def test_import_table_summary_and_exit_codes(aldwin_home: Path) -> None:
    result = runner.invoke(app, ["connectors", "import", str(IMPORTS / "cursor.json")])
    assert result.exit_code == 0, result.output
    rows = _table_rows(result.stdout)
    assert rows[0] == ["name", "type", "status"]
    assert ["github", "local", "imported"] in rows
    assert ["docs", "remote", "imported"] in rows
    assert result.stdout.splitlines()[-1] == (
        "Imported 2 connectors. 1 value moved to Aldwin's credential store."
    )

    again = runner.invoke(app, ["connectors", "import", str(IMPORTS / "cursor.json")])
    assert again.exit_code == 1
    assert "skipped: same as github" in again.stdout


@pytest.mark.parametrize(
    ("servers", "summary"),
    [
        (
            {
                "api": {
                    "command": "api-mcp",
                    "env": {"API_KEY": "fake-api-1", "LOG_LEVEL": "debug"},
                }
            },
            "Imported 1 connector. 2 values moved to Aldwin's credential store.",
        ),
        (
            {
                "one": {"command": "one-mcp", "env": {"TOKEN": "fake-tok-1"}},
                "two": {"command": "two-mcp"},
            },
            "Imported 2 connectors. 1 value moved to Aldwin's credential store.",
        ),
    ],
    ids=["single", "one_value"],
)
def test_import_summary_singular_and_plural(
    aldwin_home: Path, tmp_path: Path, servers: dict[str, Any], summary: str
) -> None:
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"mcpServers": servers}), "utf-8")
    result = runner.invoke(app, ["connectors", "import", str(path)])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[-1] == summary


@pytest.mark.parametrize(
    "kind", ["not-json", "too-big", "folder", "missing", "no-servers"]
)
def test_import_unreadable_files_exit_1(
    aldwin_home: Path, tmp_path: Path, kind: str
) -> None:
    path = tmp_path / "input.json"
    if kind == "not-json":
        path.write_text("not json", "utf-8")
    elif kind == "too-big":
        path.write_bytes(b" " * 1_048_577)
    elif kind == "folder":
        path.mkdir()
    elif kind == "no-servers":
        path.write_text('{"theme": "dark"}', "utf-8")
    result = runner.invoke(app, ["connectors", "import", str(path)])
    assert result.exit_code == 1
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1
    if kind == "no-servers":
        assert lines[0] == f"No MCP servers found in {path}."
    else:
        assert lines[0].startswith(f"{path} isn't a JSON file Aldwin can read: ")
    assert not connectors_path(aldwin_home).exists()


def test_import_table_shows_markup_literally(aldwin_home: Path, tmp_path: Path) -> None:
    path = tmp_path / "markup.json"
    servers = {"evil[/x]": {"url": "https://e.example/mcp"}}
    path.write_text(json.dumps({"mcpServers": servers}), "utf-8")
    result = runner.invoke(app, ["connectors", "import", str(path)])
    assert result.exit_code == 0, result.output
    assert "renamed from evil[/x]" in result.stdout
    assert result.stdout.splitlines()[-1].startswith("Imported 1 connector.")


def _seed_found(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    apps: dict[str, str] | None = None,
) -> Path:
    """Config files for `apps` (app name to fixture) in this test's home folder."""
    from aldwin.core.connectors.import_sources import (
        load_import_sources,
        resolve_locations,
    )

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)
    wanted = apps or {"Claude Desktop": "claude_desktop.json", "Cursor": "cursor.json"}
    sources, _ = load_import_sources()
    for source in sources:
        if source.app not in wanted:
            continue
        locations, _ = resolve_locations(source, user_home=Path.home(), workspace=ws)
        target = next(loc.path for loc in locations if loc.scope == "user")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((IMPORTS / wanted[source.app]).read_bytes())
    return ws


def test_bare_import_finds_lists_and_imports_with_yes(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_found(monkeypatch, tmp_path)
    result = runner.invoke(app, ["connectors", "import", "--yes"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "Found:"
    assert lines[1].startswith("  Claude Desktop (user)  1 server  ")
    assert lines[2].startswith("  Cursor (user)  2 servers  ")
    rows = _table_rows(result.stdout)
    assert ["name", "type", "status", "from"] in rows
    assert ["filesystem", "local", "imported", "Claude", "Desktop"] in rows
    assert lines[-1] == (
        "Imported 3 connectors. 1 value moved to Aldwin's credential store."
    )
    assert set(load_connectors(aldwin_home).entries) == {"filesystem", "github", "docs"}


def test_bare_import_needs_yes_without_tty(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_found(monkeypatch, tmp_path)
    result = runner.invoke(app, ["connectors", "import"])
    assert result.exit_code == 1
    assert result.stderr.strip() == connectors_cmd.IMPORT_NEEDS_YES
    assert "Found:" in result.stdout
    assert not connectors_path(aldwin_home).exists()


def test_bare_import_asks_and_no_imports_nothing(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tty: None
) -> None:
    _seed_found(monkeypatch, tmp_path)
    declined = runner.invoke(app, ["connectors", "import"], input="\n")
    assert declined.exit_code == 1
    assert "Nothing imported." in declined.stdout
    assert not connectors_path(aldwin_home).exists()

    accepted = runner.invoke(app, ["connectors", "import"], input="y\n")
    assert accepted.exit_code == 0, accepted.output
    assert "github" in load_connectors(aldwin_home).entries


def test_bare_import_with_nothing_found(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["connectors", "import"])
    assert result.exit_code == 1
    assert result.stderr.strip() == connectors_cmd.IMPORT_NONE_FOUND


def test_bare_import_across_three_apps(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_found(
        monkeypatch,
        tmp_path,
        {
            "Claude Code": "claude_code.json",
            "Codex CLI": "codex.toml",
            "opencode": "opencode.jsonc",
        },
    )
    result = runner.invoke(app, ["connectors", "import", "--yes"])
    assert result.exit_code == 0, result.output
    rows = _table_rows(result.stdout)
    assert ["name", "type", "status", "from"] in rows
    assert ["context7", "local", "imported", "Codex", "CLI"] in rows
    assert ["atlassian", "remote", "imported", "opencode"] in rows
    assert result.stdout.splitlines()[-1] == (
        "Imported 8 connectors. 4 values moved to Aldwin's credential store."
    )
    assert "Traceback" not in result.stderr


def test_bare_import_warns_about_an_unreadable_file_and_continues(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aldwin.core.connectors.import_sources import (
        load_import_sources,
        resolve_locations,
    )

    ws = _seed_found(monkeypatch, tmp_path)
    cursor = next(s for s in load_import_sources()[0] if s.app == "Cursor")
    locked = next(
        loc.path
        for loc in resolve_locations(cursor, user_home=Path.home(), workspace=ws)[0]
        if loc.scope == "user"
    )
    locked.chmod(0)
    try:
        result = runner.invoke(app, ["connectors", "import", "--yes"])
    finally:
        locked.chmod(0o600)
    assert result.exit_code == 0, result.output
    assert "Cursor (user)  can't read: " in result.stdout
    assert "filesystem" in result.stdout
    assert "Traceback" not in result.stderr


def test_bare_import_when_every_file_fails(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aldwin.core.connectors.import_sources import (
        load_import_sources,
        resolve_locations,
    )

    ws = _seed_found(monkeypatch, tmp_path, {"Cursor": "cursor.json"})
    cursor = next(s for s in load_import_sources()[0] if s.app == "Cursor")
    path = next(
        loc.path
        for loc in resolve_locations(cursor, user_home=Path.home(), workspace=ws)[0]
        if loc.scope == "user"
    )
    path.write_text("{not json", "utf-8")
    result = runner.invoke(app, ["connectors", "import", "--yes"])
    assert result.exit_code == 1
    assert "can't read:" in result.stdout
    assert result.stdout.splitlines()[-1] == (
        "Imported 0 connectors. 0 values moved to Aldwin's credential store."
    )
    assert "Traceback" not in result.stderr


def test_import_paths_setting_adds_a_custom_file(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_found(monkeypatch, tmp_path, {"Cursor": "cursor.json"})
    custom = tmp_path / "team.toml"
    custom.write_bytes((IMPORTS / "codex.toml").read_bytes())
    (aldwin_home / "config.yaml").write_text(
        "model: gpt-4o\nconnectors:\n"
        f"  import_paths: [{custom}, {tmp_path / 'gone.json'}]\n",
        "utf-8",
    )
    result = runner.invoke(app, ["connectors", "import", "--yes"])
    assert result.exit_code == 0, result.output
    assert f"  Custom (user)  3 servers  {custom}" in result.stdout
    assert (
        f"warning: import path {tmp_path / 'gone.json'}: no such file" in result.stderr
    )
