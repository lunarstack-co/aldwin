"""Local connector entries in `connectors.yaml`: shape, validation, names."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from aldwin.core.connectors.config import (
    ConnectorConfig,
    connectors_path,
    load_connectors,
    save_connectors,
    suggest_name_for_command,
)


def _write(home: Path, entries: dict[str, Any]) -> None:
    connectors_path(home).write_text(
        yaml.safe_dump({"version": 1, "connectors": entries}), encoding="utf-8"
    )


def test_url_and_command_together_rejected(aldwin_home: Path) -> None:
    _write(aldwin_home, {"x": {"url": "https://a.example/mcp", "command": "npx"}})
    load = load_connectors(aldwin_home)
    assert load.error == "connectors.yaml: x must have either url or command, not both"
    assert load.entries == {}


def test_entry_without_url_or_command_rejected(aldwin_home: Path) -> None:
    _write(aldwin_home, {"x": {"enabled": True}})
    assert load_connectors(aldwin_home).error == (
        "connectors.yaml: x must have url or command"
    )


def test_local_entry_round_trips_with_workspace_default(aldwin_home: Path) -> None:
    local = ConnectorConfig(
        name="pkg",
        command="npx",
        args=["-y", "pkg@1.0.0"],
        env_allow=["GITHUB_TOKEN"],
    )
    remote = ConnectorConfig(name="web", url="https://web.example/mcp")
    save_connectors(aldwin_home, {"pkg": local, "web": remote})

    entries = load_connectors(aldwin_home).entries
    assert entries == {"pkg": local, "web": remote}
    assert local.cwd == "workspace"
    assert (local.kind, remote.kind) == ("local", "remote")
    saved = yaml.safe_load(connectors_path(aldwin_home).read_text("utf-8"))
    # A remote entry's yaml carries none of the local fields.
    assert set(saved["connectors"]["web"]) == {
        "url",
        "enabled",
        "auth",
        "trust_read_only_hints",
    }


@pytest.mark.parametrize(
    "body",
    [
        {"command": "npx", "env_allow": ["1BAD"]},
        {"command": "npx", "env_allow": ["A-B"]},
        {"command": "npx", "env_allow": ["A" * 65]},
        {"command": "npx", "cwd": "relative/dir"},
        {"command": "npx", "auth": "bearer"},
        {"command": "npx", "transport": "sse"},
    ],
    ids=["digit-first", "dash", "too-long", "relative-cwd", "auth", "transport"],
)
def test_invalid_local_fields_rejected(aldwin_home: Path, body: dict[str, Any]) -> None:
    _write(aldwin_home, {"x": body})
    load = load_connectors(aldwin_home)
    assert load.error is not None and load.error.startswith("connectors.yaml: x")
    assert load.entries == {}


@pytest.mark.parametrize(
    ("argv", "existing", "expected"),
    [
        (["npx", "-y", "@modelcontextprotocol/server-everything"], set(), "everything"),
        (["uvx", "mcp-server-git"], set(), "git"),
        (["npx", "-y", "@acme/github-mcp@1.2.0"], set(), "github"),
        (
            ["npx", "-y", "@modelcontextprotocol/server-everything"],
            {"everything"},
            "everything-2",
        ),
        (["uvx", "--from", "mcp-server-git==0.6.2", "mcp-server-git"], set(), "git"),
        (["pnpm", "dlx", "@acme/notes-server"], set(), "notes"),
        (["python", "-I", "/srv/weather_server.py"], set(), "weather_server"),
        (["/usr/local/bin/my-tool"], set(), "my-tool"),
    ],
)
def test_suggest_name_for_command(
    argv: list[str], existing: set[str], expected: str
) -> None:
    assert suggest_name_for_command(argv, existing) == expected
