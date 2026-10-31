"""Policy entries for connector tools: read-only hints, per-tool settings, grants."""

from __future__ import annotations

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.mcp_tool import RemoteToolSpec
from aldwin.core.connectors.naming import model_tool_name
from aldwin.core.policy import REGISTRY, ToolPolicy

HINT_REASON = "server says read-only"


def connector_policy(
    config: ConnectorConfig, spec: RemoteToolSpec
) -> ToolPolicy | None:
    """Build the `REGISTRY` entry for one server tool, or `None` when it is off.

    A per-tool setting wins. Otherwise a tool the server marks read-only runs
    automatically (when the user trusts the hints), and everything else asks,
    with "yes for this session" allowed per tool.
    """
    setting = config.tools.get(spec.name)
    if setting == "off":
        return None
    hinted = config.trust_read_only_hints and spec.read_only_hint is True
    auto = setting == "auto" or (setting is None and hinted)
    return ToolPolicy(
        name=model_tool_name(config.name, spec.name),
        tier="auto" if auto else "ask",
        writes=not auto,
        parallel_safe=False,
        connector=config.name,
        auto_reason=HINT_REASON if auto and setting is None else None,
        session_grant=not auto,
        untrusted_source=f"mcp:{config.name}:{spec.name}",
    )


def tool_call_labels(tool_name: str) -> tuple[str | None, str | None]:
    """`(connector, reason)` for a tool row; `(None, None)` for built-in tools."""
    policy = REGISTRY.get(tool_name)
    if policy is None:
        return None, None
    return policy.connector, policy.auto_reason
