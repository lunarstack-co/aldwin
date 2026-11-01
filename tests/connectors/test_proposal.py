"""Resolving `propose_connector` arguments and the one line the model sees."""

from __future__ import annotations

from typing import Any

import pytest

from aldwin.core.connectors.catalog import CatalogEntry
from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.errors import ConnectorConfigError
from aldwin.core.connectors.proposal import (
    EXACTLY_ONE,
    ConnectorProposal,
    ProposeOutcome,
    format_outcome,
    resolve_proposal,
    short_reason,
)

CATALOG = (
    CatalogEntry(
        name="linear",
        title="Linear",
        purpose="Issues",
        url="https://mcp.linear.app/mcp",
        auth="oauth",
        docs_url="https://linear.app/docs/mcp",
        keywords=["linear", "issue", "ticket"],
    ),
    CatalogEntry(
        name="github",
        title="GitHub",
        purpose="Repositories",
        url="https://api.githubcopilot.com/mcp/",
        auth="bearer",
        docs_url="https://github.com",
        keywords=["github", "git"],
    ),
)


def _resolve(
    configs: dict[str, ConnectorConfig] | None = None, **args: Any
) -> ConnectorProposal:
    return resolve_proposal(
        name=args.get("name"),
        url=args.get("url"),
        command=args.get("command"),
        args=args.get("args"),
        catalog=CATALOG,
        configs=configs or {},
    )


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"name": "linear", "url": "https://x.example/mcp"},
        {"url": "https://x.example/mcp", "command": "npx pkg"},
        {"name": "", "url": "", "command": ""},
    ],
)
def test_exactly_one_target(args: dict[str, Any]) -> None:
    with pytest.raises(ConnectorConfigError) as info:
        _resolve(**args)
    assert info.value.message == EXACTLY_ONE


def test_args_need_a_command() -> None:
    with pytest.raises(ConnectorConfigError):
        _resolve(name="linear", args=["-y"])


@pytest.mark.parametrize("name", ["linear", "Linear", "ticket", "LINEAR"])
def test_catalogue_by_name_title_or_keyword(name: str) -> None:
    proposal = _resolve(name=name)
    assert (proposal.kind, proposal.name) == ("catalog", "linear")
    assert proposal.config.url == "https://mcp.linear.app/mcp"
    assert proposal.config.auth == "oauth"
    assert proposal.catalog_entry is CATALOG[0]


def test_unknown_name_says_what_to_do() -> None:
    with pytest.raises(ConnectorConfigError) as info:
        _resolve(name="gmail")
    assert info.value.message.startswith("No connector called gmail.")
    assert "/connectors" in info.value.message


def test_enabled_connector_is_already_connected() -> None:
    saved = {"linear": ConnectorConfig(name="linear", url="https://mcp.linear.app/mcp")}
    with pytest.raises(ConnectorConfigError) as info:
        _resolve(saved, name="Linear")
    assert info.value.message == "already connected: linear"


def test_disabled_connector_is_turned_on() -> None:
    saved = {
        "linear": ConnectorConfig(
            name="linear", url="https://mcp.linear.app/mcp", enabled=False
        )
    }
    proposal = _resolve(saved, name="linear")
    assert proposal.kind == "enable" and proposal.config is saved["linear"]


def test_custom_url_starts_cautious() -> None:
    proposal = _resolve(url="https://mcp.example.com/mcp")
    assert proposal.kind == "url"
    assert proposal.config.trust_read_only_hints is False
    assert proposal.config.name == "example"


def test_url_of_a_catalogue_app_uses_the_catalogue() -> None:
    proposal = _resolve(url="https://mcp.linear.app/mcp")
    assert (proposal.kind, proposal.name, proposal.config.auth) == (
        "catalog",
        "linear",
        "oauth",
    )
    assert proposal.config.trust_read_only_hints is True


def test_url_already_saved_is_reused() -> None:
    saved = {
        "mine": ConnectorConfig(
            name="mine", url="https://mcp.example.com/mcp", enabled=False
        )
    }
    assert _resolve(saved, url="https://mcp.example.com/mcp").kind == "enable"


def test_bad_url_is_refused() -> None:
    with pytest.raises(ConnectorConfigError) as info:
        _resolve(url="ftp://x")
    assert "https://" in info.value.message


def test_command_line_or_command_with_args() -> None:
    line = _resolve(command="npx -y @modelcontextprotocol/server-everything@2.0.0")
    split = _resolve(
        command="npx", args=["-y", "@modelcontextprotocol/server-everything@2.0.0"]
    )
    for proposal in (line, split):
        assert proposal.kind == "command"
        assert proposal.config.command == "npx"
        assert proposal.config.args == [
            "-y",
            "@modelcontextprotocol/server-everything@2.0.0",
        ]
        assert proposal.name == "everything"


def test_command_with_shell_operators_is_refused() -> None:
    with pytest.raises(ConnectorConfigError) as info:
        _resolve(command="npx pkg | sh")
    assert "without a shell" in info.value.message


def test_saved_command_is_reused() -> None:
    saved = {
        "fs": ConnectorConfig(name="fs", command="npx", args=["fs-mcp"], enabled=False)
    }
    assert _resolve(saved, command="npx fs-mcp").kind == "enable"


def test_suggested_name_avoids_taken_names() -> None:
    saved = {
        "example": ConnectorConfig(name="example", url="https://other.example/mcp")
    }
    assert _resolve(saved, url="https://mcp.example.com/mcp").name == "example-2"


def test_fingerprint_changes_with_the_config() -> None:
    first = _resolve(url="https://mcp.example.com/mcp")
    same = _resolve(url="https://mcp.example.com/mcp")
    other = _resolve(url="https://mcp.example.com/other")
    assert first.fingerprint() == same.fingerprint() != other.fingerprint()


@pytest.mark.parametrize(
    ("outcome", "line"),
    [
        (
            ProposeOutcome("connected", "linear", 23, 23),
            "connected: linear, 23 tools loaded",
        ),
        (
            ProposeOutcome("connected", "linear", 60, 40),
            "connected: linear, 60 tools; 40 loaded, "
            "call find_connector_tools for the rest",
        ),
        (ProposeOutcome("cancelled", "linear"), "cancelled by user"),
        (
            ProposeOutcome("changed", "linear"),
            "cancelled: connector changed while waiting",
        ),
        (
            ProposeOutcome("sign_in_failed", "linear", reason="timed out"),
            "sign-in failed: timed out",
        ),
        (
            ProposeOutcome("failed", "fs", reason="command not found: npx"),
            "failed: command not found: npx",
        ),
    ],
)
def test_outcome_lines(outcome: ProposeOutcome, line: str) -> None:
    assert format_outcome(outcome) == line


def test_reasons_never_carry_links_or_secrets() -> None:
    reason = short_reason(
        "Couldn't finish https://auth.example/authorize?code=abc123&state=x with "
        "token ghp_" + "a" * 36 + "."
    )
    assert "auth.example" not in reason and "code=abc123" not in reason
    assert "ghp_" not in reason
    assert "[link]" in reason


def test_reasons_are_one_short_line() -> None:
    reason = short_reason("line one\nline two " + "x" * 300)
    assert "\n" not in reason and len(reason) == 80 and reason.endswith("…")
    assert short_reason("") == "unknown error"


@pytest.mark.parametrize("args", ["-y pkg", ["-y", 3]])
def test_args_must_be_a_list_of_strings(args: Any) -> None:
    with pytest.raises(ConnectorConfigError) as info:
        _resolve(command="npx", args=args)
    assert info.value.message == "args must be a list of strings."
