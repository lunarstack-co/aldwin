"""Live model provider tests across first-ship backends.

Run by hand with:
    uv run pytest -m live tests/live/test_providers_live.py
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from agentling import FinalEvent, Model
from agentling.testing import check_model
from pydantic import SecretStr

from aldwin.core.agent import build_agent, new_session
from aldwin.core.home import ensure_home
from aldwin.core.providers.factory import get_backend
from aldwin.core.providers.registry import load_builtin_providers
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.core.tools.calculate import CalculateTool

PROVIDERS = {p.name: p for p in load_builtin_providers()}

LIVE_CANDIDATES: list[tuple[str, str, str | None]] = [
    ("openai", "gpt-4o-mini", "OPENAI_API_KEY"),
    ("anthropic", "claude-3-5-haiku-20241022", "ANTHROPIC_API_KEY"),
    ("gemini", "gemini-2.0-flash", "GEMINI_API_KEY"),
    ("openrouter", "google/gemini-2.0-flash-lite-preview:free", "OPENROUTER_API_KEY"),
    ("deepseek", "deepseek-chat", "DEEPSEEK_API_KEY"),
    ("ollama", "qwen2.5:0.5b", None),
    ("lmstudio", "qwen2.5-0.5b-instruct", None),
]


async def _is_local_reachable(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(base_url)
            return resp.status_code < 500
    except Exception:
        return False


async def _deny_ask(_s: str, _d: str = "") -> bool:
    return False


@pytest.mark.live
@pytest.mark.parametrize(("provider_name", "model_id", "env_var"), LIVE_CANDIDATES)
async def test_provider_live_turn(
    aldwin_home: Path,
    provider_name: str,
    model_id: str,
    env_var: str | None,
) -> None:
    """Opt-in live check_model and calculate tool turn per provider."""
    entry = PROVIDERS[provider_name]

    api_key: str | None = None
    if env_var:
        api_key = os.environ.get(env_var, "").strip()
        if not api_key:
            pytest.skip(f"No {env_var} set in environment.")
    else:
        if not await _is_local_reachable(entry.base_url):
            pytest.skip(f"{entry.title} server not running at {entry.base_url}.")

    ensure_home(aldwin_home)

    def factory(transport: httpx.AsyncBaseTransport | None) -> Model:
        secrets = (
            SecretsConfig(**{entry.secret: SecretStr(api_key)})
            if entry.secret and api_key
            else SecretsConfig()
        )
        return get_backend(entry, secrets=secrets, transport=transport).build_model(
            model_id
        )

    report = await check_model(factory, live=True)
    report.raise_for_failures()

    secrets_cfg = (
        SecretsConfig(**{entry.secret: SecretStr(api_key)})
        if entry.secret and api_key
        else SecretsConfig()
    )
    full_ref = f"{provider_name}/{model_id}"
    agent = build_agent(
        AldwinConfig(model=full_ref),
        secrets_cfg,
        _deny_ask,
        home=aldwin_home,
        tools=[CalculateTool()],
    )
    session = new_session(agent)

    final_event: FinalEvent | None = None
    async for ev in session.run(
        "Calculate 17 * 23 using the calculate tool.", stream=True
    ):
        if isinstance(ev, FinalEvent):
            final_event = ev

    assert final_event is not None
    assert final_event.status == "completed"
    assert "391" in final_event.answer
