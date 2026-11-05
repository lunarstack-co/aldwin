"""Security tests verifying secret redaction in fixtures and logs."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.host.in_process import InProcessTransport
from aldwin.host.runner import AldwinHost
from aldwin.protocol import ProviderKeyRequest
from aldwin.protocol.secret_shapes import SECRET_SHAPES

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "providers"


def test_recorded_fixtures_contain_no_secrets() -> None:
    """Recorded provider fixtures do not contain credentials matching SECRET_SHAPES."""
    fixture_files = list(FIXTURES_DIR.glob("*/fixture.jsonl"))
    assert len(fixture_files) >= 7, (
        f"Expected at least 7 fixtures, found {len(fixture_files)}"
    )

    compiled = [(label, re.compile(pattern)) for label, pattern in SECRET_SHAPES]

    for f_path in fixture_files:
        text = f_path.read_text(encoding="utf-8")
        assert "Bearer sk-" not in text
        assert "sk-ant-api" not in text
        for label, pattern in compiled:
            matches = pattern.findall(text)
            real_leaks = [
                m for m in matches if not m.startswith(("call_", "chatcmpl-"))
            ]
            assert not real_leaks, (
                f"Secret pattern {label} found in {f_path}: {real_leaks}"
            )


async def test_provider_key_request_canary_not_leaked(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Canary key in ProviderKeyRequest is never leaked to logs, sessions, or events."""
    canary = "sk-ant-api03-canary7f3a99118822xx"
    req = ProviderKeyRequest(provider="anthropic", key=canary)

    assert canary not in repr(req)
    assert canary not in str(req)

    # Set up host with mock transport that rejects key
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_api_key"})

    mock_transport = httpx.MockTransport(handler)
    transport = InProcessTransport()
    fake_model.add_turns(FakeTurn.text(["Turn finished."]))
    host = AldwinHost(
        transport,
        home=aldwin_home,
        backend_transport=mock_transport,
        agent_builder=lambda m, ask: build_agent(
            AldwinConfig(model=m),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        ),
    )
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()

    try:
        # Submit canary key
        res = await client.save_provider_key("anthropic", canary)
        # Key must not appear in error message
        assert canary not in getattr(res, "message", "")

        # Run a turn
        async for _ in client.turn("Hello aldwin"):
            pass
    finally:
        await client.close()
        await host.stop_host()

    # Multi-surface scan
    log_path = aldwin_home / "logs" / "aldwin.log"
    if log_path.exists():
        assert canary not in log_path.read_text(encoding="utf-8")

    approvals_path = aldwin_home / "logs" / "approvals.jsonl"
    if approvals_path.exists():
        assert canary not in approvals_path.read_text(encoding="utf-8")

    for session_file in (aldwin_home / "sessions").glob("*.json"):
        assert canary not in session_file.read_text(encoding="utf-8")
