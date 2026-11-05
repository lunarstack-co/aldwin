"""Tests for /model command grouping, status rows, and switching."""

from __future__ import annotations

import io

import pytest
from fakes.host import FakeClient

from aldwin.cli.commands import SLASH_COMMANDS, handle_slash_command
from aldwin.cli.render import ChatRenderer
from aldwin.cli.theme import create_console
from aldwin.protocol import (
    ModelEntry,
    ModelsEvent,
    ProviderModels,
)


def _make_models_event(current: str = "openai/gpt-4o") -> ModelsEvent:
    return ModelsEvent(
        re="req_1",
        current=current,
        providers=[
            ProviderModels(
                name="ollama",
                title="Ollama",
                local=True,
                configured=True,
                status="ok",
                models=[
                    ModelEntry(
                        ref="ollama/qwen3:8b",
                        id="qwen3:8b",
                        title="qwen3:8b",
                        context_window=32768,
                        supports_tools=True,
                    ),
                    ModelEntry(
                        ref="ollama/deepseek-r1:8b",
                        id="deepseek-r1:8b",
                        title="deepseek-r1:8b",
                        context_window=16384,
                        supports_tools=False,
                    ),
                ],
            ),
            ProviderModels(
                name="lmstudio",
                title="LM Studio",
                local=True,
                configured=True,
                status="not_running",
                models=[],
            ),
            ProviderModels(
                name="openai",
                title="OpenAI",
                local=False,
                configured=True,
                status="ok",
                models=[
                    ModelEntry(
                        ref="openai/gpt-4o",
                        id="gpt-4o",
                        title="gpt-4o",
                        context_window=128000,
                        supports_tools=True,
                    ),
                    ModelEntry(
                        ref="openai/gpt-4o-2024-11-20",
                        id="gpt-4o-2024-11-20",
                        title="gpt-4o-2024-11-20",
                        context_window=128000,
                        supports_tools=True,
                    ),
                ],
            ),
            ProviderModels(
                name="anthropic",
                title="Anthropic",
                local=False,
                configured=True,
                status="ok",
                models=[
                    ModelEntry(
                        ref="anthropic/claude-sonnet-4-5",
                        id="claude-sonnet-4-5",
                        title="claude-sonnet-4-5",
                        context_window=200000,
                        supports_tools=True,
                    )
                ],
            ),
            ProviderModels(
                name="deepseek",
                title="DeepSeek",
                local=False,
                configured=False,
                status="not_configured",
                models=[],
            ),
        ],
    )


async def test_help_command_shows_switch_model_or_provider() -> None:
    """The /help description for /model is 'Switch model or provider'."""
    match = next((desc for cmd, desc in SLASH_COMMANDS if cmd == "/model"), None)
    assert match == "Switch model or provider"


async def test_typed_model_switching_and_formatting() -> None:
    """Typed /model formats confirmation with title and local."""
    client = FakeClient(
        model="gpt-4o",
        available_models=["gpt-4o", "claude-sonnet-4-5", "qwen3:8b", "gpt-5"],
    )
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    async def _empty(_label: str) -> str:
        return ""

    # Cloud qualified
    await handle_slash_command(
        "/model anthropic/claude-sonnet-4-5",
        client=client,
        renderer=renderer,
        prompt_line=_empty,
    )
    assert "✓ Switched to claude-sonnet-4-5 (Anthropic)." in console.export_text()

    # Cloud bare ID -> OpenAI
    await handle_slash_command(
        "/model gpt-5",
        client=client,
        renderer=renderer,
        prompt_line=_empty,
    )
    assert "✓ Switched to gpt-5 (OpenAI)." in console.export_text()

    # Local qualified -> (Ollama, local)
    await handle_slash_command(
        "/model ollama/qwen3:8b",
        client=client,
        renderer=renderer,
        prompt_line=_empty,
    )
    assert "✓ Switched to qwen3:8b (Ollama, local)." in console.export_text()


async def test_model_picker_grouping_and_display() -> None:
    """Collapsed providers list on screen 1, models on screen 2, esc goes back."""
    client = FakeClient()
    client.models_event = _make_models_event()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    # Screen 1: pick Ollama (1) -> Screen 2: see models, Enter to go back -> cancel
    answers = iter(["1", "", ""])

    async def _nav(_label: str) -> str:
        return next(answers)

    await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=_nav,
    )
    text = console.export_text()

    # Screen 1 shows providers
    assert "Available providers" in text
    assert "1. Ollama" in text
    assert "2. LM Studio" in text
    assert "3. OpenAI (current)" in text
    assert "4. Anthropic" in text
    assert "5. Add a provider…" in text
    assert "6. Other server" in text

    # Screen 2 shows Ollama models with metadata
    assert "Ollama models" in text
    assert "qwen3:8b" in text
    assert "✓ tools · 32K" in text
    assert "deepseek-r1:8b" in text
    assert "✗ chat only · 16K" in text


async def test_model_picker_canonical_filtering() -> None:
    """Dated snapshots hidden in OpenAI group, shown by /model all."""
    client = FakeClient()
    client.models_event = _make_models_event()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    # Pick OpenAI (3) -> go back (Enter) -> cancel on Screen 1 (Enter)
    answers1 = iter(["3", "", ""])

    async def _ans1(_label: str) -> str:
        return next(answers1)

    await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=_ans1,
    )
    text = console.export_text()
    assert "gpt-4o-2024-11-20" not in text
    assert "showing 1 canonical · use /model all for all 2" in text

    buf_all = io.StringIO()
    console_all = create_console(plain=True, width=80, record=True, file=buf_all)
    renderer_all = ChatRenderer(console_all, plain=True)

    answers2 = iter(["3", "", ""])

    async def _ans2(_label: str) -> str:
        return next(answers2)

    await handle_slash_command(
        "/model all",
        client=client,
        renderer=renderer_all,
        prompt_line=_ans2,
    )
    text_all = console_all.export_text()
    assert "gpt-4o-2024-11-20" in text_all


async def test_no_tools_model_warning_cancel() -> None:
    """Picking no-tools model shows warning; 'n' cancels."""
    client = FakeClient(available_models=["qwen3:8b", "deepseek-r1:8b"])
    client.models_event = _make_models_event()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    # Screen 1: Ollama (1) -> Screen 2: deepseek (2) -> Warning: n -> cancel
    answers = iter(["1", "2", "n", ""])

    async def _prompt(_label: str) -> str:
        return next(answers)

    await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=_prompt,
    )
    text = console.export_text()
    assert "Warning: deepseek-r1:8b does not support tools" in text
    assert "Model unchanged" in text


async def test_no_tools_model_warning_confirm() -> None:
    """Picking no-tools model shows warning; 'y' switches."""
    client = FakeClient(available_models=["qwen3:8b", "deepseek-r1:8b"])
    client.models_event = _make_models_event()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    # Screen 1: Ollama (1) -> Screen 2: deepseek-r1:8b (2) -> Warning: y
    answers = iter(["1", "2", "y"])

    async def _prompt(_label: str) -> str:
        return next(answers)

    await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=_prompt,
    )
    text = console.export_text()
    assert "Warning: deepseek-r1:8b does not support tools" in text
    assert "✓ Switched to deepseek-r1:8b (Ollama, local)." in text


async def test_add_provider_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Add a provider…' prompts unconfigured list and key, then saves."""
    client = FakeClient()
    client.models_event = _make_models_event()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    import aldwin.cli.secret_prompt as sp_mod

    async def fake_prompt_secret(_label: str) -> str:
        return "sk-deepseek-test-key"

    monkeypatch.setattr(sp_mod, "prompt_secret", fake_prompt_secret)
    # Screen 1: Add a provider (5) -> DeepSeek (1) -> Screen 1 loop: cancel
    answers = iter(["5", "1", ""])

    async def _prompt(_label: str) -> str:
        return next(answers)

    await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=_prompt,
    )
    assert any(
        req[0] == "provider.key" and req[1] == "deepseek" for req in client.requests
    )
    assert "✓ Saved key for DeepSeek." in console.export_text()


async def test_add_other_server_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Other server' prompts address, key, name, and saves server."""
    client = FakeClient()
    client.models_event = _make_models_event()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    # Screen 1: Other server (6) -> address -> name -> Screen 1 loop: cancel
    answers = iter(["6", "http://127.0.0.1:8000/v1", "office-gpu", ""])

    async def _prompt(_label: str) -> str:
        return next(answers)

    import aldwin.cli.secret_prompt as sp_mod

    async def fake_empty_secret(_label: str) -> str:
        return ""

    monkeypatch.setattr(sp_mod, "prompt_secret", fake_empty_secret)
    await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=_prompt,
    )
    assert any(
        req[0] == "provider.server" and req[1] == "office-gpu"
        for req in client.requests
    )
    assert "✓ Added provider office-gpu." in console.export_text()
