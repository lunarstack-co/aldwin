"""Unit tests for agent wiring with multi-provider backends."""

from pathlib import Path

import pytest

from aldwin.core.agent import build_agent
from aldwin.core.home import ensure_home
from aldwin.core.providers.anthropic import AnthropicMessagesModel
from aldwin.core.providers.ollama import OllamaModel
from aldwin.core.settings import AldwinConfig, ConfigError, SecretsConfig
from aldwin.host.in_process import list_models_for


def test_build_agent_provider_selection(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure_home(aldwin_home)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    config = AldwinConfig(model="anthropic/claude-3-7-sonnet")
    secrets = SecretsConfig(anthropic_api_key="test-anthropic-key")
    agent = build_agent(config, secrets, home=aldwin_home)
    assert isinstance(agent.model, AnthropicMessagesModel)
    assert agent.model.model == "claude-3-7-sonnet"


def test_build_agent_missing_key_error(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure_home(aldwin_home)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = AldwinConfig(model="deepseek/deepseek-chat")
    secrets = SecretsConfig()
    with pytest.raises(ConfigError) as exc_info:
        build_agent(config, secrets, home=aldwin_home)
    assert (
        "No API key for DeepSeek. Run `aldwin setup` or set DEEPSEEK_API_KEY."
        in str(exc_info.value)
    )


def test_build_agent_local_model_no_key(
    aldwin_home: Path,
) -> None:
    ensure_home(aldwin_home)
    config = AldwinConfig(model="ollama/llama3.1")
    secrets = SecretsConfig()
    agent = build_agent(config, secrets, home=aldwin_home)
    assert isinstance(agent.model, OllamaModel)
    assert agent.model.model == "llama3.1"


async def test_list_models_for_failure_handling(
    aldwin_home: Path,
) -> None:
    ensure_home(aldwin_home)
    models = await list_models_for("invalid_provider_name", home=aldwin_home)
    assert models == []
