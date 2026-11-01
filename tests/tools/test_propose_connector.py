"""`propose_connector` on its own, with fake resolve, preview and propose."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agentling.tools import ToolCallError

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.errors import ConnectorConfigError
from aldwin.core.connectors.proposal import ConnectorProposal, ProposeOutcome
from aldwin.core.policy import REGISTRY, ApprovalPreview
from aldwin.core.tools import connector_tools
from aldwin.core.tools.connector_tools import ProposeConnectorTool

PREVIEW = ApprovalPreview(summary="Aldwin wants to add the Plain connector", detail="d")


class Fakes:
    def __init__(self) -> None:
        self.url = "https://plain.example/mcp"
        self.proposed: list[tuple[ConnectorProposal, str | None]] = []
        self.outcome = ProposeOutcome("connected", "plain", tools=3, loaded=3)
        self.delay = 0.0
        self.digest: str | None = None

    def resolve(self, args: dict[str, Any]) -> ConnectorProposal:
        if args.get("name") == "taken":
            raise ConnectorConfigError("already connected: taken")
        # A url argument resolves to itself unless the test moved `self.url`.
        url = self.url if args.get("url") == "https://plain.example/mcp" else None
        return ConnectorProposal(
            "url", ConnectorConfig(name="plain", url=url or args["url"])
        )

    def preview(
        self, proposal: ConnectorProposal
    ) -> tuple[ApprovalPreview, str | None]:
        return PREVIEW, self.digest

    async def propose(
        self, proposal: ConnectorProposal, digest: str | None
    ) -> ProposeOutcome:
        self.proposed.append((proposal, digest))
        await asyncio.sleep(self.delay)
        return self.outcome


@pytest.fixture
def fakes() -> Fakes:
    return Fakes()


@pytest.fixture
def tool(fakes: Fakes) -> ProposeConnectorTool:
    return ProposeConnectorTool(
        resolve=fakes.resolve, preview=fakes.preview, propose=fakes.propose
    )


def test_tool_shape_and_policy(tool: ProposeConnectorTool) -> None:
    assert tool.name == "propose_connector"
    assert tool.timeout == 400.0 and tool.parallel_safe is False
    policy = REGISTRY["propose_connector"]
    assert (policy.tier, policy.never_grant, policy.parallel_safe) == (
        "ask",
        "connectors",
        False,
    )
    assert "Never ask the user to type or paste a token" in tool.description


async def test_preview_then_run(tool: ProposeConnectorTool, fakes: Fakes) -> None:
    fakes.digest = "sha256:abc"
    assert tool.prepare_approval(url=fakes.url) is PREVIEW
    result = await tool.forward(url=fakes.url)
    assert result == "connected: plain, 3 tools loaded"
    assert fakes.proposed[0][1] == "sha256:abc"


def test_resolve_errors_reach_the_model_before_any_approval(
    tool: ProposeConnectorTool,
) -> None:
    with pytest.raises(ToolCallError) as info:
        tool.prepare_approval(name="taken")
    assert str(info.value) == "already connected: taken"


async def test_changed_between_approval_and_run(
    tool: ProposeConnectorTool, fakes: Fakes
) -> None:
    tool.prepare_approval(url=fakes.url)
    fakes.url = "https://elsewhere.example/mcp"
    result = await tool.forward(url="https://plain.example/mcp")
    assert result == "cancelled: connector changed while waiting"
    assert fakes.proposed == []


async def test_never_runs_without_a_preview(
    tool: ProposeConnectorTool, fakes: Fakes
) -> None:
    result = await tool.forward(url=fakes.url)
    assert result == "cancelled: connector changed while waiting"
    assert fakes.proposed == []


async def test_the_whole_proposal_is_capped(
    tool: ProposeConnectorTool, fakes: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(connector_tools, "PROPOSE_CAP_S", 0.05)
    fakes.delay = 5.0
    tool.prepare_approval(url=fakes.url)
    assert await tool.forward(url=fakes.url) == "sign-in failed: timed out"


async def test_declined_previews_do_not_pile_up(
    tool: ProposeConnectorTool, fakes: Fakes
) -> None:
    for n in range(20):
        tool.prepare_approval(url=f"https://n{n}.example/mcp")
    assert len(tool._previewed) == connector_tools.MAX_PREVIEWS
    tool.prepare_approval(url=fakes.url)
    assert await tool.forward(url=fakes.url) == "connected: plain, 3 tools loaded"


async def test_two_approved_proposals_both_run(
    tool: ProposeConnectorTool, fakes: Fakes
) -> None:
    first, second = "https://plain.example/mcp", "https://other.example/mcp"
    tool.prepare_approval(url=first)
    tool.prepare_approval(url=second)
    assert (await tool.forward(url=first)).startswith("connected")
    assert (await tool.forward(url=second)).startswith("connected")
    assert [p.config.url for p, _ in fakes.proposed] == [first, second]
