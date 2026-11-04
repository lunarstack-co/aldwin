"""Conformance suite testing model adapters with check_model."""

from pathlib import Path

import httpx
import pytest
from agentling.testing import check_model

from aldwin.core.providers import get_backend
from aldwin.core.settings import SecretsConfig

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "providers"


@pytest.mark.parametrize(
    ("provider_name", "model_id", "fixture_subdir"),
    [
        ("openai", "gpt-4o-mini", "openai"),
        ("anthropic", "claude-sonnet-5", "anthropic"),
        ("gemini", "gemini-2.5-flash", "gemini"),
        ("openrouter", "openrouter/anthropic/claude-3-7-sonnet", "openrouter"),
        ("deepseek", "deepseek-chat", "deepseek"),
        ("ollama", "qwen3:8b", "ollama"),
        ("lmstudio", "qwen3-8b", "lmstudio"),
    ],
)
async def test_provider_check_model_conformance(
    provider_name: str,
    model_id: str,
    fixture_subdir: str,
) -> None:
    fixture_path = FIXTURES_DIR / fixture_subdir / "fixture.jsonl"
    assert fixture_path.exists(), f"Missing fixture at {fixture_path}"

    def factory(transport: httpx.AsyncBaseTransport | None):
        secrets = SecretsConfig(
            openai_api_key="fake-openai-key",
            anthropic_api_key="fake-anthropic-key",
            gemini_api_key="fake-gemini-key",
            openrouter_api_key="fake-openrouter-key",
            deepseek_api_key="fake-deepseek-key",
        )
        backend = get_backend(provider_name, secrets=secrets, transport=transport)
        return backend.build_model(model_id)

    report = await check_model(factory, recorded=fixture_path)
    report.raise_for_failures()
    assert report.ok is True
