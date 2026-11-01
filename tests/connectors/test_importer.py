"""Importing Claude Desktop, Cursor and VS Code MCP configs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from aldwin.core.connectors import importer
from aldwin.core.connectors.config import (
    ConnectorConfig,
    connectors_path,
    load_connectors,
    save_connectors,
)
from aldwin.core.connectors.credentials import FileCredentialStore
from aldwin.core.connectors.errors import ConnectorConfigError
from aldwin.core.connectors.importer import (
    MAX_IMPORT_BYTES,
    ImportResult,
    read_config_file,
    strip_jsonc,
)
from aldwin.host.connectors import ConnectorAdmin

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "import"


def _import(
    home: Path, path: Path, *, rename: bool = False, workspace: Path | None = None
) -> ImportResult:
    return ConnectorAdmin(home).import_file(
        path, rename_conflicts=rename, workspace=workspace or home / "ws"
    )


def _write(tmp_path: Path, servers: dict[str, Any], key: str = "mcpServers") -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({key: servers}), "utf-8")
    return path


def _yaml_entry(home: Path, name: str) -> Any:
    return yaml.safe_load(connectors_path(home).read_text("utf-8"))["connectors"][name]


def _rows(result: ImportResult) -> dict[str, tuple[str, str]]:
    return {r.name: (r.status, r.detail) for r in result.rows}


def test_cursor_file_imports_local_disabled_and_moves_token(aldwin_home: Path) -> None:
    result = _import(aldwin_home, FIXTURES / "cursor.json")
    assert (result.imported, result.values) == (2, 1)
    assert _rows(result) == {
        "github": ("imported", "imported"),
        "docs": ("imported", "imported"),
    }
    entries = load_connectors(aldwin_home).entries
    github, docs = entries["github"], entries["docs"]
    assert (github.kind, github.enabled, github.note) == (
        "local",
        False,
        "review and enable",
    )
    assert github.env_allow == ["GITHUB_TOKEN"] and github.approved_digest is None
    assert docs.enabled and docs.url == "https://docs.example.com/mcp"
    assert FileCredentialStore(aldwin_home).get("github") == {
        "v": 1,
        "kind": "env",
        "env": {"GITHUB_TOKEN": "ghp_fake0001"},
    }
    assert "ghp_fake0001" not in connectors_path(aldwin_home).read_text("utf-8")


def test_vscode_jsonc_parses_like_cursor(tmp_path: Path) -> None:
    cursor_home, vscode_home = tmp_path / "a", tmp_path / "b"
    _import(cursor_home, FIXTURES / "cursor.json")
    _import(vscode_home, FIXTURES / "vscode.jsonc")
    assert load_connectors(vscode_home).entries == load_connectors(cursor_home).entries
    docs = load_connectors(vscode_home).entries["docs"]
    assert docs.url == "https://docs.example.com/mcp"


def test_strip_jsonc_keeps_strings_intact() -> None:
    text = '{"a": "x // y /* z */", /* c */ "b": [1, 2,], // tail\n}'
    assert json.loads(strip_jsonc(text)) == {"a": "x // y /* z */", "b": [1, 2]}


def test_existing_name_is_skipped_unchanged(aldwin_home: Path) -> None:
    save_connectors(
        aldwin_home,
        {"github": ConnectorConfig(name="github", url="https://old.example.com/mcp")},
    )
    before = _yaml_entry(aldwin_home, "github")
    result = _import(aldwin_home, FIXTURES / "cursor.json")
    assert _rows(result)["github"] == ("skipped", "skipped: name already exists")
    assert FileCredentialStore(aldwin_home).get("github") is None
    entries = load_connectors(aldwin_home).entries
    assert entries["github"].url == "https://old.example.com/mcp"
    assert "docs" in entries
    assert _yaml_entry(aldwin_home, "github") == before


def test_sse_entry_imported_as_failed(aldwin_home: Path) -> None:
    result = _import(aldwin_home, FIXTURES / "sse.json")
    assert _rows(result) == {"old": ("failed", "failed: SSE is not supported")}
    assert result.imported == 1
    assert load_connectors(aldwin_home).entries["old"].transport == "sse"
    assert "transport: sse" in connectors_path(aldwin_home).read_text("utf-8")


def test_claude_desktop_file(aldwin_home: Path) -> None:
    result = _import(aldwin_home, FIXTURES / "claude_desktop.json")
    assert [r.name for r in result.rows] == ["filesystem"]
    fs = load_connectors(aldwin_home).entries["filesystem"]
    assert fs.kind == "local" and not fs.enabled
    assert fs.args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]


def test_env_value_forms(aldwin_home: Path, tmp_path: Path) -> None:
    env = {
        "A": "${env:A}",
        "B": "${B}",
        "C": "${input:c}",
        "D": "${env:OTHER}",
        "DATABASE_URL": "postgres://u:fakepass@localhost/db",
        "LOG_LEVEL": "debug",
        "DEBUG": True,
        "PORT": 8080,
    }
    path = _write(tmp_path, {"db": {"command": "db-mcp", "env": env}})
    result = _import(aldwin_home, path)
    assert result.values == 4
    db = load_connectors(aldwin_home).entries["db"]
    assert db.env_allow == ["A", "B", "DATABASE_URL", "LOG_LEVEL", "DEBUG", "PORT"]
    stored = FileCredentialStore(aldwin_home).get("db")
    assert stored is not None
    assert set(stored["env"]) == {"DATABASE_URL", "LOG_LEVEL", "DEBUG", "PORT"}
    # JSON scalars keep the spelling the server expects, not Python's.
    assert (stored["env"]["DEBUG"], stored["env"]["PORT"]) == ("true", "8080")
    assert db.note is not None
    assert "set C by hand" in db.note and "set D by hand" in db.note
    text = connectors_path(aldwin_home).read_text("utf-8")
    assert "fakepass" not in text and "debug" not in text


def test_headers_bearer_kept_others_noted(aldwin_home: Path, tmp_path: Path) -> None:
    servers = {
        "a": {
            "url": "https://a.example/mcp",
            "headers": {"Authorization": "Bearer fake-bearer-1"},
        },
        "b": {"url": "https://b.example/mcp", "headers": {"X-API-Key": "fake-key-2"}},
        "c": {"command": "c-mcp", "envFile": ".env"},
    }
    _import(aldwin_home, _write(tmp_path, servers))
    entries = load_connectors(aldwin_home).entries
    store = FileCredentialStore(aldwin_home)
    assert entries["a"].auth == "bearer"
    assert store.get("a") == {"v": 1, "kind": "bearer", "token": "fake-bearer-1"}
    assert entries["b"].auth == "none" and "X-API-Key" in (entries["b"].note or "")
    assert store.get("b") is None
    assert "envFile" in (entries["c"].note or "")
    text = connectors_path(aldwin_home).read_text("utf-8")
    assert "fake-bearer-1" not in text and "fake-key-2" not in text
    saved = [p.read_text("utf-8") for p in store.directory.glob("*.json")]
    assert not any("fake-key-2" in body for body in saved)


def test_disabled_flags_and_remote_aliases(aldwin_home: Path, tmp_path: Path) -> None:
    servers = {
        "off1": {"url": "https://one.example/mcp", "disabled": True},
        "off2": {"url": "https://two.example/mcp", "isActive": False},
        "s1": {"serverUrl": "https://s1.example/mcp", "type": "http"},
        "s2": {"httpUrl": "https://s2.example/mcp", "type": "streamable-http"},
        "s3": {"baseUrl": "https://s3.example/mcp", "type": "streamableHttp"},
        "s4": {"url": "https://s4.example/mcp", "type": "remote"},
        "bad": {"url": "ftp://x"},
    }
    result = _import(aldwin_home, _write(tmp_path, servers))
    entries = load_connectors(aldwin_home).entries
    assert not entries["off1"].enabled and not entries["off2"].enabled
    assert all(entries[n].url for n in ("s1", "s2", "s3", "s4"))
    assert _rows(result)["bad"] == (
        "failed",
        "failed: Connector URL must use https:// (got ftp://x)",
    )
    assert "bad" not in entries


def test_command_list_split(aldwin_home: Path, tmp_path: Path) -> None:
    path = _write(tmp_path, {"pkg": {"command": ["npx", "-y", "pkg@1.0.0"]}})
    _import(aldwin_home, path)
    pkg = load_connectors(aldwin_home).entries["pkg"]
    assert (pkg.command, pkg.args) == ("npx", ["-y", "pkg@1.0.0"])


def test_rename_conflicts_and_slug_collisions(
    aldwin_home: Path, tmp_path: Path
) -> None:
    save_connectors(
        aldwin_home,
        {
            n: ConnectorConfig(name=n, url=f"https://{n}.example/mcp")
            for n in ("github", "github-2")
        },
    )
    server = {"url": "https://gh.example/mcp"}
    path = _write(tmp_path, {"github": server, "GitHub": server, "Git Hub!": server})

    renamed = _import(aldwin_home, path, rename=True)
    assert [r.name for r in renamed.rows] == ["github-3", "github-4", "git-hub"]
    for row in renamed.rows:
        assert f"renamed from {row.source_name}" in row.detail

    again = _import(aldwin_home, path)
    statuses = {r.source_name: r.detail for r in again.rows}
    assert statuses["github"] == statuses["GitHub"] == "skipped: name already exists"


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda p: p.write_text("not json", "utf-8"), "expecting value"),
        (lambda p: p.write_bytes(b"{" + b" " * MAX_IMPORT_BYTES), "larger than 1 MB"),
        (lambda p: p.mkdir(), "it is a folder"),
        (lambda p: None, "no such file"),
        (lambda p: p.write_bytes(b"\xff\xfe\x00"), "isn't UTF-8 text"),
    ],
    ids=["not-json", "too-big", "folder", "missing", "binary"],
)
def test_unreadable_files(tmp_path: Path, make: Any, reason: str) -> None:
    path = tmp_path / "config.json"
    make(path)
    with pytest.raises(ConnectorConfigError) as info:
        read_config_file(path)
    assert info.value.message.startswith(f"{path} isn't a JSON file Aldwin can read: ")
    assert reason in info.value.message and info.value.message.endswith(".")


def test_file_without_servers(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"editor.fontSize": 12}', "utf-8")
    with pytest.raises(ConnectorConfigError) as info:
        read_config_file(path)
    assert info.value.message == f"No MCP servers found in {path}."


def test_yaml_failure_leaves_no_dangling_entry(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(importer, "save_connectors", fail)
        with pytest.raises(OSError):
            _import(aldwin_home, FIXTURES / "cursor.json")
    assert not connectors_path(aldwin_home).exists()
    store = FileCredentialStore(aldwin_home)
    assert store.get("github") is not None

    store.set("github", {"v": 1, "kind": "env", "env": {"GITHUB_TOKEN": "stale"}})
    assert _import(aldwin_home, FIXTURES / "cursor.json").imported == 2
    stored = store.get("github")
    assert stored is not None and stored["env"]["GITHUB_TOKEN"] == "ghp_fake0001"


# --- finding and reading other apps' files -----------------------------------------


def _place(
    app: str, fixture: str | bytes, *, workspace: Path, scope: str = "user"
) -> Path:
    """Put `fixture` (a fixture name or raw bytes) where `app` keeps its file."""
    from aldwin.core.connectors.import_sources import (
        load_import_sources,
        resolve_locations,
    )

    sources, _ = load_import_sources()
    source = next(s for s in sources if s.app == app)
    locations, _ = resolve_locations(source, user_home=Path.home(), workspace=workspace)
    target = next(loc.path for loc in locations if loc.scope == scope)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (FIXTURES / fixture).read_bytes() if isinstance(fixture, str) else fixture
    target.write_bytes(data)
    return target


def _find(workspace: Path | None, **kw: Any) -> tuple[list[Any], list[str]]:
    return importer.find_config_files(user_home=Path.home(), workspace=workspace, **kw)


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ws


def test_claude_code_imports_user_and_this_workspace_only(
    aldwin_home: Path, tmp_path: Path
) -> None:
    ws = _ws(tmp_path)
    other = tmp_path / "other"
    raw = {
        "mcpServers": {"a": {"command": "a-mcp"}},
        "projects": {
            str(ws.resolve()): {"mcpServers": {"b": {"command": "b-mcp"}}},
            str(other): {"mcpServers": {"c": {"command": "c-mcp"}}},
        },
    }
    path = _place("Claude Code", json.dumps(raw).encode(), workspace=ws)
    result = _import(aldwin_home, path, workspace=ws)
    assert sorted(r.name for r in result.rows) == ["a", "b"]
    assert set(load_connectors(aldwin_home).entries) == {"a", "b"}


def test_claude_code_workspace_matches_through_a_symlink(
    aldwin_home: Path, tmp_path: Path
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    raw = {"projects": {str(real): {"mcpServers": {"b": {"command": "b-mcp"}}}}}
    path = _place("Claude Code", json.dumps(raw).encode(), workspace=link)
    result = _import(aldwin_home, path, workspace=link)
    assert [r.name for r in result.rows] == ["b"]


def test_size_caps_are_per_source(aldwin_home: Path, tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    padding = " " * 2_000_000
    big = (
        '{"mcpServers": {"a": {"command": "a-mcp"}}, "pad": "' + padding + '"}'
    ).encode()
    claude = _place("Claude Code", big, workspace=ws)
    cursor = _place("Cursor", big, workspace=ws)
    assert _import(aldwin_home, claude, workspace=ws).imported == 1
    with pytest.raises(ConnectorConfigError) as info:
        _import(aldwin_home, cursor, workspace=ws)
    assert info.value.message == (
        f"{cursor} isn't a JSON file Aldwin can read: it is larger than 1 MB."
    )


def test_toml_and_yaml_parse_errors(aldwin_home: Path, tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    codex = _place("Codex CLI", b"not = toml =\n", workspace=ws)
    hermes = _place("Hermes Agent", b"mcp_servers: [\n", workspace=ws)
    with pytest.raises(ConnectorConfigError) as info:
        _import(aldwin_home, codex, workspace=ws)
    assert info.value.message.startswith(f"{codex} isn't a TOML file Aldwin can read: ")
    with pytest.raises(ConnectorConfigError) as info:
        _import(aldwin_home, hermes, workspace=ws)
    assert info.value.message.startswith(
        f"{hermes} isn't a YAML file Aldwin can read: "
    )


def test_unsupported_types_fail_per_row(aldwin_home: Path, tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = _place("Claude Code", "claude_code.json", workspace=ws)
    rows = _rows(_import(aldwin_home, path, workspace=ws))
    assert rows["sockets"] == ("failed", "failed: type ws is not supported")
    assert rows["embedded"] == ("failed", "failed: type sdk is not supported")
    assert rows["legacy"] == ("failed", "failed: SSE is not supported")
    assert rows["github"] == ("imported", "imported")
    stripe = load_connectors(aldwin_home).entries["stripe"]
    assert (stripe.auth, stripe.token_env) == ("bearer", "STRIPE_KEY")
    assert FileCredentialStore(aldwin_home).get("stripe") is None


def test_copilot_local_type_and_devin_transport(
    aldwin_home: Path, tmp_path: Path
) -> None:
    ws = _ws(tmp_path)
    copilot = _place("GitHub Copilot CLI", "copilot_cli.json", workspace=ws)
    devin = _place("Devin", "devin.json", workspace=ws)
    _import(aldwin_home, copilot, workspace=ws)
    rows = _rows(_import(aldwin_home, devin, workspace=ws))
    entries = load_connectors(aldwin_home).entries
    assert entries["playwright"].kind == "local" and entries["playwright"].tools == {}
    assert rows["legacy"] == ("failed", "failed: SSE is not supported")
    assert entries["notion"].enabled and entries["notion"].transport is None
    assert not entries["fs"].enabled


def test_gemini_url_is_sse_and_trust_is_dropped(
    aldwin_home: Path, tmp_path: Path
) -> None:
    ws = _ws(tmp_path)
    path = _place("Gemini CLI", "gemini_cli.json", workspace=ws)
    rows = _rows(_import(aldwin_home, path, workspace=ws))
    entries = load_connectors(aldwin_home).entries
    assert rows["legacy-sse"] == ("failed", "failed: SSE is not supported")
    assert entries["docs"].url == "https://docs.example.com/mcp"
    assert entries["docs"].transport is None
    tools = entries["python-tools"]
    assert tools.env_allow == ["MODE", "LOG"]
    assert "set API_KEY by hand" in (tools.note or "")
    assert tools.tools == {} and not tools.trust_read_only_hints or tools.tools == {}
    stored = FileCredentialStore(aldwin_home).get("python-tools")
    assert stored is not None and stored["env"] == {"LOG": "debug"}


def test_opencode_shape(aldwin_home: Path, tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = _place("opencode", "opencode.jsonc", workspace=ws)
    _import(aldwin_home, path, workspace=ws)
    entries = load_connectors(aldwin_home).entries
    pw = entries["playwright"]
    assert (pw.command, pw.args) == ("npx", ["-y", "@playwright/mcp@0.0.84"])
    assert pw.env_allow == ["PW_TOKEN", "HEADLESS"] and not pw.enabled
    store = FileCredentialStore(aldwin_home)
    assert store.get("playwright") == {
        "v": 1,
        "kind": "env",
        "env": {"HEADLESS": "true"},
    }
    atl = entries["atlassian"]
    assert atl.url == "https://mcp.atlassian.com/v1/mcp" and not atl.enabled
    assert store.get("atlassian") == {"v": 1, "kind": "bearer", "token": "fake-atl-1"}


def test_codex_shape(aldwin_home: Path, tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = _place("Codex CLI", "codex.toml", workspace=ws)
    result = _import(aldwin_home, path, workspace=ws)
    assert (result.imported, result.values) == (3, 1)
    entries = load_connectors(aldwin_home).entries
    store = FileCredentialStore(aldwin_home)
    c7 = entries["context7"]
    assert c7.env_allow == ["MY_ENV_VAR", "LOCAL_TOKEN"] and c7.cwd == "/tmp"
    assert store.get("context7") == {
        "v": 1,
        "kind": "env",
        "env": {"MY_ENV_VAR": "fake-env-1"},
    }
    figma = entries["figma"]
    assert (figma.auth, figma.token_env) == ("bearer", "FIGMA_OAUTH_TOKEN")
    assert "skipped header X-Figma-Region" in (figma.note or "")
    assert store.get("figma") is None
    linear = entries["linear"]
    assert (linear.token_env, linear.enabled) == ("LINEAR_TOKEN", False)
    assert "skipped header X-Trace" in (linear.note or "")


def test_zed_both_command_spellings(aldwin_home: Path, tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = _place("Zed", "zed.json", workspace=ws)
    _import(aldwin_home, path, workspace=ws)
    entries = load_connectors(aldwin_home).entries
    assert (entries["local-mcp-server"].command, entries["local-mcp-server"].args) == (
        "some-command",
        ["arg-1", "arg-2"],
    )
    assert (entries["older-form"].command, entries["older-form"].args) == (
        "/usr/local/bin/older-mcp",
        ["--port", "0"],
    )
    assert entries["remote-mcp-server"].auth == "bearer"


def test_goose_shape(aldwin_home: Path, tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = _place("Goose", "goose.yaml", workspace=ws)
    rows = _rows(_import(aldwin_home, path, workspace=ws))
    assert rows["developer"] == ("skipped", "skipped: built into Goose")
    assert rows["summoner"] == ("skipped", "skipped: built into Goose")
    entries = load_connectors(aldwin_home).entries
    fetch = entries["fetch"]
    assert (fetch.command, fetch.args) == ("uvx", ["mcp-server-fetch@2026.10.28"])
    assert fetch.env_allow == ["FETCH_TOKEN", "HTTP_PROXY"]
    docs = entries["remote-docs"]
    assert docs.url == "https://docs.example.com/mcp" and not docs.enabled
    store = FileCredentialStore(aldwin_home)
    assert store.get("fetch") == {
        "v": 1,
        "kind": "env",
        "env": {"FETCH_TOKEN": "fake-goose-1"},
    }
    assert store.get("remote-docs") == {
        "v": 1,
        "kind": "bearer",
        "token": "fake-goose-2",
    }


@pytest.mark.parametrize(
    ("app", "fixture", "expected"),
    [
        ("Hermes Agent", "hermes_agent.yaml", {"time", "notion", "github"}),
        ("nanobot", "nanobot.json", {"filesystem", "remote"}),
        ("Pi", "pi.json", {"docs", "github", "sentry"}),
        ("Cline", "cline.json", {"weather", "remote-tools"}),
        ("VS Code", "vscode.jsonc", {"github", "docs"}),
        ("Cursor", "cursor.json", {"github", "docs"}),
        ("Claude Desktop", "claude_desktop.json", {"filesystem"}),
    ],
)
def test_standard_shaped_apps_import(
    aldwin_home: Path, tmp_path: Path, app: str, fixture: str, expected: set[str]
) -> None:
    ws = _ws(tmp_path)
    path = _place(app, fixture, workspace=ws)
    result = _import(aldwin_home, path, workspace=ws)
    assert {r.name for r in result.rows if r.status == "imported"} == expected
    assert result.app == app


@pytest.mark.parametrize(
    "fixture", sorted(p.name for p in FIXTURES.iterdir() if p.suffix != ".md")
)
def test_canary_from_any_app_stays_in_the_store(
    aldwin_home: Path, tmp_path: Path, fixture: str
) -> None:
    from canary import CANARY, scan_for_canary

    from aldwin.core.connectors.config import credentials_dir

    text = (FIXTURES / fixture).read_text("utf-8")
    poisoned = re.sub(r"fake-[a-z0-9-]+|ghp_[A-Za-z0-9]+", CANARY, text)
    if CANARY not in poisoned:
        pytest.skip("fixture holds no secret value")
    path = tmp_path / fixture
    path.write_text(poisoned, "utf-8")
    result = _import(aldwin_home, path, workspace=_ws(tmp_path))
    hits = scan_for_canary(
        [connectors_path(aldwin_home), aldwin_home / "logs"],
        [json.dumps([r.__dict__ for r in result.rows]), "\n".join(result.warnings)],
    )
    assert hits == []
    stored = "".join(
        p.read_text("utf-8") for p in credentials_dir(aldwin_home).glob("*.json")
    )
    assert CANARY in stored


def test_reference_forms(aldwin_home: Path, tmp_path: Path) -> None:
    env = {
        "A": "$A",
        "B": "${B}",
        "C": "${env:C}",
        "D": "{env:D}",
        "E": "${E:-fallback}",
        "F": "$G",
        "H": "${input:h}",
        "I": "!echo secret-cmd",
        "J": "pa$$word",
        "K": "cost$5",
        "L": "a{env:B}c",
    }
    path = _write(tmp_path, {"svc": {"command": "svc-mcp", "env": env}})
    result = _import(aldwin_home, path)
    assert result.values == 3
    svc = load_connectors(aldwin_home).entries["svc"]
    assert svc.env_allow == ["A", "B", "C", "D", "E", "J", "K", "L"]
    note = svc.note or ""
    for expected in (
        "default for E not imported",
        "set F by hand",
        "set H by hand",
        "set I by hand",
    ):
        assert expected in note
    stored = FileCredentialStore(aldwin_home).get("svc")
    assert stored is not None
    assert stored["env"] == {"J": "pa$$word", "K": "cost$5", "L": "a{env:B}c"}
    everything = connectors_path(aldwin_home).read_text("utf-8") + json.dumps(stored)
    assert "fallback" not in everything and "secret-cmd" not in everything


def test_bearer_from_a_variable(aldwin_home: Path, tmp_path: Path) -> None:
    servers = {
        "a": {
            "url": "https://a.example/mcp",
            "headers": {"Authorization": "Bearer ${GH_TOKEN}"},
        },
        "b": {
            "url": "https://b.example/mcp",
            "headers": {"Authorization": "Bearer {env:GH_TOKEN}"},
        },
        "c": {
            "url": "https://c.example/mcp",
            "headers": {"Authorization": "${WHOLE_HEADER}"},
        },
    }
    _import(aldwin_home, _write(tmp_path, servers))
    entries = load_connectors(aldwin_home).entries
    for name in ("a", "b"):
        assert (entries[name].auth, entries[name].token_env) == ("bearer", "GH_TOKEN")
    assert entries["c"].auth == "none" and "skipped header Authorization" in (
        entries["c"].note or ""
    )
    assert list(FileCredentialStore(aldwin_home).directory.glob("*.json")) == []


def test_project_files_import_disabled(aldwin_home: Path, tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    project = {
        "mcpServers": {
            "loc": {"command": "loc-mcp"},
            "rem": {"url": "https://r.example/mcp"},
        }
    }
    user = {"mcpServers": {"user-rem": {"url": "https://u.example/mcp"}}}
    project_path = _place(
        "Claude Code", json.dumps(project).encode(), workspace=ws, scope="project"
    )
    user_path = _place("Claude Code", json.dumps(user).encode(), workspace=ws)
    _import(aldwin_home, project_path, workspace=ws)
    _import(aldwin_home, user_path, workspace=ws)
    entries = load_connectors(aldwin_home).entries
    for name in ("loc", "rem"):
        assert not entries[name].enabled and entries[name].note == "review and enable"
    assert entries["user-rem"].enabled and entries["user-rem"].note is None


def test_identical_servers_are_skipped_not_renamed(
    aldwin_home: Path, tmp_path: Path
) -> None:
    same = ConnectorConfig(
        name="github",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github@1.0.0"],
    )
    different = ConnectorConfig(name="docs", url="https://elsewhere.example/mcp")
    save_connectors(aldwin_home, {"github": same, "docs": different})
    plain = _rows(_import(aldwin_home, FIXTURES / "cursor.json"))
    assert plain["github"] == ("skipped", "skipped: same as github")
    assert plain["docs"] == ("skipped", "skipped: name already exists")
    renamed = _rows(_import(aldwin_home, FIXTURES / "cursor.json", rename=True))
    assert renamed["github"] == ("skipped", "skipped: same as github")
    assert renamed["docs-2"] == ("imported", "imported (renamed from docs)")
    assert "github-2" not in load_connectors(aldwin_home).entries


def test_find_lists_apps_in_filename_order_with_counts(
    aldwin_home: Path, tmp_path: Path
) -> None:
    ws = _ws(tmp_path)
    _place("opencode", "opencode.jsonc", workspace=ws)
    _place("Claude Code", "claude_code.json", workspace=ws)
    _place("Codex CLI", "codex.toml", workspace=ws)
    found, warnings = _find(ws)
    assert warnings == []
    assert [(f.app, f.scope, f.servers) for f in found] == [
        ("Claude Code", "user", 5),
        ("Codex CLI", "user", 3),
        ("opencode", "user", 2),
    ]


def test_find_lists_a_shared_file_once(aldwin_home: Path, tmp_path: Path) -> None:
    from aldwin.core.connectors.import_sources import (
        load_import_sources,
        resolve_locations,
    )

    ws = _ws(tmp_path)
    standalone = _place("Cline", "cline.json", workspace=ws)
    source = next(s for s in load_import_sources()[0] if s.app == "Cline")
    locations, _ = resolve_locations(source, user_home=Path.home(), workspace=ws)
    extension = locations[1].path
    extension.parent.mkdir(parents=True)
    extension.symlink_to(standalone)
    found, _ = _find(ws)
    assert [f.app for f in found] == ["Cline"]


def test_extra_paths_are_custom_and_detected(aldwin_home: Path, tmp_path: Path) -> None:
    custom = tmp_path / "team" / "mcp.toml"
    custom.parent.mkdir()
    custom.write_bytes((FIXTURES / "codex.toml").read_bytes())
    found, warnings = _find(
        _ws(tmp_path), extra_paths=[str(custom), str(tmp_path / "missing.json")]
    )
    assert [(f.app, f.scope, f.servers) for f in found] == [("Custom", "user", 3)]
    assert warnings == [f"import path {tmp_path / 'missing.json'}: no such file"]
    loaded = importer.load_file(custom, user_home=Path.home(), workspace=_ws(tmp_path))
    assert loaded.dialect == "codex"


def test_discovery_never_starts_anything(aldwin_home: Path, tmp_path: Path) -> None:
    from aldwin.core.connectors import stdio

    ws = _ws(tmp_path)
    _place("Claude Code", "claude_code.json", workspace=ws)
    found, _ = _find(ws)
    _import(aldwin_home, found[0].path, workspace=ws)
    assert not stdio._LIVE_PGIDS
    assert all(
        not c.enabled
        for c in load_connectors(aldwin_home).entries.values()
        if c.kind == "local"
    )


def test_explicit_paths_are_detected_by_content(
    aldwin_home: Path, tmp_path: Path
) -> None:
    renamed = tmp_path / "foo.json"
    renamed.write_bytes((FIXTURES / "opencode.jsonc").read_bytes())
    goose = tmp_path / "g.yaml"
    goose.write_bytes((FIXTURES / "goose.yaml").read_bytes())
    ws = _ws(tmp_path)
    assert (
        importer.load_file(renamed, user_home=Path.home(), workspace=ws).dialect
        == "opencode"
    )
    assert (
        importer.load_file(goose, user_home=Path.home(), workspace=ws).dialect
        == "goose"
    )
    plain = tmp_path / "plain.json"
    plain.write_text('{"editor.fontSize": 12}', "utf-8")
    with pytest.raises(ConnectorConfigError) as info:
        importer.load_file(plain, user_home=Path.home(), workspace=ws)
    assert info.value.message == f"No MCP servers found in {plain}."


def test_no_workspace_warns_and_user_files_still_import(
    aldwin_home: Path, tmp_path: Path
) -> None:
    _place("Cursor", "cursor.json", workspace=_ws(tmp_path))
    found, warnings = _find(None)
    assert [f.app for f in found] == ["Cursor"]
    assert "Cursor: can't resolve {workspace}/.cursor/mcp.json" in warnings


def test_unreadable_file_is_reported_and_others_import(
    aldwin_home: Path, tmp_path: Path
) -> None:
    ws = _ws(tmp_path)
    locked = _place("Cursor", "cursor.json", workspace=ws)
    _place("Claude Desktop", "claude_desktop.json", workspace=ws)
    locked.chmod(0)
    try:
        found, _ = _find(ws)
    finally:
        locked.chmod(0o600)
    by_app = {f.app: f for f in found}
    assert (
        by_app["Cursor"].error is not None
        and "Permission denied" in by_app["Cursor"].error
    )
    assert by_app["Claude Desktop"].servers == 1


def test_missing_servers_key_is_zero_servers_not_a_warning(
    aldwin_home: Path, tmp_path: Path
) -> None:
    # A settings file with no servers in it is normal, not a problem.
    ws = _ws(tmp_path)
    _place("nanobot", b'{"tools": {}}', workspace=ws)
    found, warnings = _find(ws)
    assert (found[0].servers, found[0].error, warnings) == (0, None, [])


def test_deep_nesting_is_one_error_not_a_crash(
    aldwin_home: Path, tmp_path: Path
) -> None:
    ws = _ws(tmp_path)
    _place("Cursor", b"[" * 100_000, workspace=ws)
    found, warnings = _find(ws)
    assert found[0].error is not None
    assert (
        "nested too deeply" in found[0].error or "expecting" in found[0].error.lower()
    )
    assert warnings == []


def test_unexpected_error_in_one_file_warns_and_logs(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aldwin.core.logging import setup_logging

    setup_logging(aldwin_home)
    ws = _ws(tmp_path)
    bad = _place("Cursor", "cursor.json", workspace=ws)
    _place("Claude Desktop", "claude_desktop.json", workspace=ws)
    original = importer.read_source_file

    def explode(path: Path, **kw: Any) -> Any:
        if path == bad:
            raise RuntimeError("boom")
        return original(path, **kw)

    monkeypatch.setattr(importer, "read_source_file", explode)
    found, warnings = _find(ws)
    assert [f.app for f in found] == ["Claude Desktop", "Cursor"]
    assert warnings == [f"Cursor: couldn't read {bad} (RuntimeError)"]
    log = (aldwin_home / "logs" / "aldwin.log").read_text("utf-8")
    assert "Traceback" in log and "boom" in log


def test_canary_in_a_broken_file_never_reaches_warnings_or_logs(
    aldwin_home: Path, tmp_path: Path
) -> None:
    from canary import CANARY

    from aldwin.core.logging import setup_logging

    setup_logging(aldwin_home)
    ws = _ws(tmp_path)
    _place(
        "Cursor",
        f'{{"mcpServers": {{"a": {{"env": {{"T": "{CANARY}"}}}},'.encode(),
        workspace=ws,
    )
    found, warnings = _find(ws)
    text = json.dumps([f.__dict__ for f in found], default=str) + "\n".join(warnings)
    assert CANARY not in text
    log = aldwin_home / "logs" / "aldwin.log"
    assert not log.exists() or CANARY not in log.read_text("utf-8")


def test_custom_path_at_a_known_location_uses_that_app(tmp_path: Path) -> None:
    other_home = tmp_path / "other"
    cursor = other_home / ".cursor" / "mcp.json"
    cursor.parent.mkdir(parents=True)
    cursor.write_bytes((FIXTURES / "cursor.json").read_bytes())
    found, warnings = importer.find_config_files(
        user_home=other_home, workspace=None, extra_paths=[str(cursor)]
    )
    assert warnings == [w for w in warnings if "can't resolve" in w]
    assert [(f.app, f.servers) for f in found] == [("Cursor", 2)]


def test_unsupported_type_text_is_capped(aldwin_home: Path, tmp_path: Path) -> None:
    servers = {"odd": {"url": "https://o.example/mcp", "type": "x" * 500}}
    result = _import(aldwin_home, _write(tmp_path, servers))
    detail = result.rows[0].detail
    assert detail.startswith("failed: type xxxx") and len(detail) < 80
