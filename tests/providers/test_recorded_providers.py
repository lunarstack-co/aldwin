"""Recorded offline tests for first-ship providers."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from agentling import Model
from agentling.testing import check_model

from aldwin.core.providers.factory import get_backend
from aldwin.core.providers.registry import load_builtin_providers

PROVIDERS = {p.name: p for p in load_builtin_providers()}

RECORDED_CASES: list[tuple[str, str]] = [
    ("openai", "gpt-4o-mini"),
    ("anthropic", "claude-3-5-haiku-20241022"),
    ("gemini", "gemini-2.0-flash"),
    ("openrouter", "google/gemini-2.0-flash-lite-preview:free"),
    ("deepseek", "deepseek-chat"),
    ("ollama", "qwen2.5:0.5b"),
    ("lmstudio", "qwen2.5-0.5b-instruct"),
]


@pytest.mark.parametrize(("provider_name", "model_id"), RECORDED_CASES)
async def test_recorded_provider_conformance_and_tool_call(
    provider_name: str,
    model_id: str,
) -> None:
    """Provider executes text and tool turns offline via fixture."""
    fixture_path = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "providers"
        / provider_name
        / "fixture.jsonl"
    )
    assert fixture_path.exists(), f"Missing fixture at {fixture_path}"

    entry = PROVIDERS[provider_name]

    def factory(transport: httpx.AsyncBaseTransport | None) -> Model:
        return get_backend(entry, transport=transport).build_model(model_id)

    report = await check_model(factory, recorded=fixture_path)
    report.raise_for_failures()
