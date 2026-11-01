"""`connectors.yaml` loading, saving and checks (`FR-1`, `FR-3`)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from aldwin.core.connectors.config import (
    ConnectorConfig,
    check_name,
    check_url,
    connectors_fingerprint,
    connectors_path,
    credentials_dir,
    load_connectors,
    save_connectors,
    suggest_name,
)
from aldwin.core.connectors.errors import ConnectorConfigError


def _write(home: Path, text: str) -> None:
    connectors_path(home).write_text(text, encoding="utf-8")


def test_missing_file_has_no_entries(aldwin_home: Path) -> None:
    load = load_connectors(aldwin_home)
    assert load.entries == {} and load.error is None


def test_round_trip_keeps_every_field(aldwin_home: Path) -> None:
    github = ConnectorConfig(
        name="github",
        url="https://api.githubcopilot.com/mcp/",
        title="GitHub",
        auth="bearer",
        token_env="GITHUB_TOKEN",
        trust_read_only_hints=False,
        tools={"delete_repository": "off", "create_issue": "auto"},
    )
    ctx = ConnectorConfig(name="context7", url="https://mcp.context7.com/mcp")
    save_connectors(aldwin_home, {"github": github, "context7": ctx})

    load = load_connectors(aldwin_home)
    assert load.error is None
    assert load.entries == {"context7": ctx, "github": github}
    mode = stat.S_IMODE(os.stat(connectors_path(aldwin_home)).st_mode)
    assert mode == 0o600


def test_saved_file_quotes_off_and_holds_no_token(aldwin_home: Path) -> None:
    config = ConnectorConfig(
        name="github",
        url="https://api.githubcopilot.com/mcp/",
        auth="bearer",
        tools={"delete_repository": "off"},
    )
    save_connectors(aldwin_home, {"github": config})
    text = connectors_path(aldwin_home).read_text(encoding="utf-8")
    assert "delete_repository: 'off'" in text
    assert "token:" not in text
    assert text.startswith("version: 1\n")


def test_bare_off_reads_as_off(aldwin_home: Path) -> None:
    _write(
        aldwin_home,
        "connectors:\n  x:\n    url: https://x.example/mcp\n    tools:\n      t: off\n",
    )
    load = load_connectors(aldwin_home)
    assert load.entries["x"].tools == {"t": "off"}


def test_defaults(aldwin_home: Path) -> None:
    _write(aldwin_home, "connectors:\n  ctx:\n    url: https://mcp.context7.com/mcp\n")
    entry = load_connectors(aldwin_home).entries["ctx"]
    assert entry.enabled is True
    assert entry.auth == "none"
    assert entry.trust_read_only_hints is True
    assert entry.tools == {}
    assert entry.display_title == "Ctx"


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (
            "connectors:\n  x:\n    url: https://x.example/mcp\n    enabled: maybe\n",
            "connectors.yaml: connectors.x.enabled must be a boolean",
        ),
        (
            "connectors:\n  x:\n    url: https://x.example/mcp\n    auth: magic\n",
            "connectors.yaml: connectors.x.auth must be one of the allowed values",
        ),
        (
            "connectors:\n  x:\n    enabled: true\n",
            "connectors.yaml: connectors.x.url must be set",
        ),
        (
            "connectors:\n  x:\n    url: http://example.com/mcp\n",
            "connectors.yaml: connectors.x.url must use https://",
        ),
        (
            "connectors:\n  Bad Name:\n    url: https://x.example/mcp\n",
            "connectors.yaml: connectors.Bad Name must be a valid name "
            "(lowercase letters, digits, - and _)",
        ),
        ("connectors: [1, 2]\n", "connectors.yaml: connectors must be a mapping"),
        ("- just\n- a list\n", "connectors.yaml: the file must be a mapping"),
        ("connectors: {x: [\n", "connectors.yaml: the file must be valid YAML"),
    ],
)
def test_invalid_file_gives_error_and_no_entries(
    aldwin_home: Path, body: str, error: str
) -> None:
    _write(aldwin_home, body)
    load = load_connectors(aldwin_home)
    assert load.entries == {}
    assert load.error == error


def test_invalid_file_is_logged_once(
    aldwin_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(aldwin_home, "connectors:\n  once:\n    url: ftp://x\n")
    load_connectors(aldwin_home)
    load_connectors(aldwin_home)
    hits = [r for r in caplog.records if "connectors.once.url" in r.getMessage()]
    assert len(hits) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://mcp.context7.com/mcp",
        "http://localhost:8080/mcp",
        "http://127.0.0.1:47823/mcp",
    ],
)
def test_check_url_allows(url: str) -> None:
    check_url(url)


@pytest.mark.parametrize(
    "url", ["http://example.com", "ftp://x.example/mcp", "mcp.context7.com", "https://"]
)
def test_check_url_refuses_with_exact_message(url: str) -> None:
    with pytest.raises(ConnectorConfigError) as info:
        check_url(url)
    assert info.value.message == f"Connector URL must use https:// (got {url})"


def test_check_name_duplicate_and_pattern() -> None:
    check_name("github", ["context7"])
    with pytest.raises(ConnectorConfigError) as dup:
        check_name("github", ["github"])
    assert dup.value.message == "A connector named github already exists"
    for bad in ("GitHub", "-x", "a" * 31, ""):
        with pytest.raises(ConnectorConfigError):
            check_name(bad, [])


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://mcp.context7.com/mcp", "context7"),
        ("https://api.githubcopilot.com/mcp/", "githubcopilot"),
        ("http://localhost:8080/mcp", "localhost"),
        ("https://mcp.notion.com/mcp", "notion"),
        ("https://", "connector"),
    ],
)
def test_suggest_name(url: str, expected: str) -> None:
    assert suggest_name(url, []) == expected


def test_suggest_name_avoids_taken_names() -> None:
    url = "https://mcp.context7.com/mcp"
    assert suggest_name(url, ["context7"]) == "context7-2"
    assert suggest_name(url, ["context7", "context7-2"]) == "context7-3"


def test_fingerprint_changes_on_config_and_credentials_not_locks(
    aldwin_home: Path,
) -> None:
    before = connectors_fingerprint(aldwin_home)
    creds = credentials_dir(aldwin_home)
    creds.mkdir(parents=True)
    (creds / "x.lock").write_text("", encoding="utf-8")
    assert connectors_fingerprint(aldwin_home) == before

    (creds / "x.json").write_text("{}", encoding="utf-8")
    with_cred = connectors_fingerprint(aldwin_home)
    assert with_cred != before

    os.utime(creds / "x.lock", ns=(1, 1))
    assert connectors_fingerprint(aldwin_home) == with_cred

    _write(aldwin_home, "connectors: {}\n")
    assert connectors_fingerprint(aldwin_home) != with_cred
