"""Security audit ensuring loopback models make zero non-loopback calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from agentling import Agent

from aldwin.client import AldwinClient
from aldwin.core.home import ensure_home
from aldwin.core.providers.ollama import OllamaModel
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import DoneEvent, Event, TextEvent


class LoopbackOnlyTransport(httpx.AsyncBaseTransport):
    """Transport enforcing that no requests ever leave loopback hosts."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.non_loopback_requests: list[str] = []
        self.loopback_requests: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host not in ("127.0.0.1", "localhost", "::1"):
            self.non_loopback_requests.append(str(request.url))
            raise AssertionError(
                f"Security violation: Non-loopback network call to {request.url}"
            )

        self.loopback_requests.append(str(request.url))
        return await self._handler(request)


@pytest.mark.asyncio
async def test_local_turn_zero_non_loopback_traffic(
    aldwin_home: Path, tmp_path: Path
) -> None:
    """Local model with check_for_updates: false makes zero outside calls."""
    ensure_home(aldwin_home)
    (aldwin_home / "config.yaml").write_text(
        "model: ollama/qwen3:8b\ncheck_for_updates: false\n",
        encoding="utf-8",
    )

    # Prepare a test file for read_file in workspace
    test_file = tmp_path / "notes.txt"
    test_file.write_text("Secret local content\n", encoding="utf-8")

    chat_turn = 0

    async def mock_ollama_handler(request: httpx.Request) -> httpx.Response:
        nonlocal chat_turn
        chat_turn += 1

        def _ndjson(lines: list[dict[str, Any]]) -> bytes:
            return "\n".join(json.dumps(x) for x in lines).encode("utf-8") + b"\n"

        if chat_turn == 1:
            # First turn: call read_file
            chunks = [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "read_file",
                                    "arguments": {"path": str(test_file)},
                                },
                            }
                        ],
                    },
                    "done": False,
                },
                {"done": True, "done_reason": "stop"},
            ]
            return httpx.Response(200, content=_ndjson(chunks))

        # Second turn: final answer
        chunks = [
            {"message": {"role": "assistant", "content": "I read the file locally."}},
            {
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 20,
                "eval_count": 8,
            },
        ]
        return httpx.Response(200, content=_ndjson(chunks))

    transport = LoopbackOnlyTransport(mock_ollama_handler)
    local_model = OllamaModel(
        model="qwen3:8b",
        base_url="http://127.0.0.1:11434",
        transport=transport,
    )

    in_process = InProcessTransport()
    host = AldwinHost(
        in_process,
        agent_builder=lambda _m, _ask: Agent(model=local_model),
        initial_model="ollama/qwen3:8b",
    )
    await host.start()
    client = AldwinClient(in_process.connect())

    try:
        await client.connect()
        events: list[Event] = []
        async for ev in client.turn("read the notes file"):
            events.append(ev)

        assert any(isinstance(e, TextEvent) for e in events)
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reason == "answered"

        # Assert no requests left loopback
        assert transport.non_loopback_requests == []
        assert len(transport.loopback_requests) > 0
    finally:
        await client.close()
        await host.stop_host()


def test_privacy_docs_fully_local_section() -> None:
    """docs/privacy.md contains the Fully local section and boundaries."""
    privacy_doc = Path("docs/privacy.md").read_text(encoding="utf-8")
    assert "## Fully local" in privacy_doc
    assert "127.0.0.1" in privacy_doc
    assert "check_for_updates: false" in privacy_doc
    assert "web_search" in privacy_doc
    assert "Zero conversation data leaves your machine" in privacy_doc
