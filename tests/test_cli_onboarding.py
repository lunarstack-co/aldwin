"""Unit and integration tests for first-run onboarding and `aldwin setup`."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from aldwin.cli.onboarding import needs_onboarding, run_onboarding, run_setup
from aldwin.cli.theme import create_console
from aldwin.client import (
    AccessStatus,
    BootstrapResult,
    KeyCheckResult,
    LocalProviderStatus,
    ModelInfo,
    bootstrap,
    get_onboarding_status,
)


@pytest.mark.uninitialized_home
async def test_onboarding_bad_key_then_good_key(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloud branch with rejected key shows error copy, then succeeds."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    boot = bootstrap(plain=True)
    assert boot.created is True
    assert needs_onboarding(boot) is True

    # 1: Cloud provider, 1: OpenAI, bad key, good key, 1: model, "": skip Tavily
    answers = iter(["1", "1", "sk-bad-key", "sk-proj-12345678good", "1", ""])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    async def fake_checker(key: str) -> KeyCheckResult:
        return "ok" if key.endswith("good") else "invalid"

    async def fake_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return []

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        key_checker=fake_checker,
        local_detector=fake_detector,
    )

    assert ok is True
    text = console.export_text()
    assert "ALDWIN" in text
    assert "That key was rejected by OpenAI. Check it and paste it again." in text
    assert "Checking key… sk-…good ✓" in text
    assert "Default model: openai/gpt-4o (change any time with /model)" in text
    assert f"Created {boot.display_home}" in text

    status = get_onboarding_status(aldwin_home)
    assert status.has_openai_key is True
    assert status.openai_source == "file"
    assert status.masked_openai_key == "sk-…good"
    assert ((aldwin_home / "secrets.yaml").stat().st_mode & 0o777) == 0o600


@pytest.mark.uninitialized_home
async def test_onboarding_env_skip_error_variants_and_three_strikes(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid env key skips prompt and local detector; error copy & 3-strike exit."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-valid-12345678")
    boot = bootstrap(plain=True)
    assert boot.created is True
    assert needs_onboarding(boot) is True

    prompt_called = False

    async def should_not_prompt(_label: str, _is_password: bool) -> str:
        nonlocal prompt_called
        prompt_called = True
        return ""

    async def env_ok_checker(_key: str) -> KeyCheckResult:
        return "ok"

    async def detector_spy(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        raise AssertionError("Local detection should be skipped with env key")

    buf_env = io.StringIO()
    console_env = create_console(plain=True, width=80, record=True, file=buf_env)
    ok_env = await run_onboarding(
        boot,
        console_env,
        prompt_fn=should_not_prompt,
        key_checker=env_ok_checker,
        local_detector=detector_spy,
    )
    assert ok_env is True
    assert prompt_called is False
    assert f"Created {boot.display_home}" in console_env.export_text()

    # Invalid env key prints error, prompts interactively, and updates os.environ
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-broken")
    env_fallback_inputs = iter(["1", "1", "sk-env-fixed-99999999", "1", ""])

    async def env_fallback_prompt(_label: str, _is_password: bool) -> str:
        return next(env_fallback_inputs)

    async def env_fallback_checker(key: str) -> KeyCheckResult:
        return "ok" if "fixed" in key else "invalid"

    async def no_locals(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return []

    buf_env_fallback = io.StringIO()
    console_env_fallback = create_console(
        plain=True, width=80, record=True, file=buf_env_fallback
    )
    assert (
        await run_onboarding(
            boot,
            console_env_fallback,
            prompt_fn=env_fallback_prompt,
            key_checker=env_fallback_checker,
            local_detector=no_locals,
        )
        is True
    )
    assert "OPENAI_API_KEY in environment failed" in console_env_fallback.export_text()
    assert get_onboarding_status(aldwin_home).masked_openai_key == "sk-…9999"

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    results: list[KeyCheckResult] = ["no_access", "network_error", "invalid"]
    results_iter = iter(results)
    inputs = iter(["1", "1", "sk-no-access", "sk-net-err", ""])

    async def strike_prompt(_label: str, _is_password: bool) -> str:
        return next(inputs)

    async def strike_checker(_key: str) -> KeyCheckResult:
        return next(results_iter)

    buf_fail = io.StringIO()
    console_fail = create_console(plain=True, width=80, record=True, file=buf_fail)
    ok_fail = await run_onboarding(
        boot,
        console_fail,
        prompt_fn=strike_prompt,
        key_checker=strike_checker,
        local_detector=no_locals,
    )
    assert ok_fail is False
    fail_text = console_fail.export_text()
    assert "That key doesn't have access to OpenAI chat models." in fail_text
    assert "Can't reach OpenAI to verify your key." in fail_text
    assert "That key was rejected by OpenAI." in fail_text
    assert "Could not verify an OpenAI key after 3 attempts." in fail_text


@pytest.mark.uninitialized_home
async def test_path_a_local_picker_and_no_key_prompt(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh home with multiple local runtimes displays per-app headings."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def mock_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return [
            LocalProviderStatus(
                name="ollama",
                title="Ollama",
                base_url="http://127.0.0.1:11434",
                running=True,
                models=(
                    ModelInfo(
                        id="qwen3:8b",
                        title="qwen3:8b",
                        context_window=32768,
                        supports_tools=True,
                        local=True,
                    ),
                ),
            ),
            LocalProviderStatus(
                name="lmstudio",
                title="LM Studio",
                base_url="http://127.0.0.1:1234/v1",
                running=True,
                models=(
                    ModelInfo(
                        id="qwen2.5-coder",
                        title="qwen2.5-coder",
                        context_window=32768,
                        supports_tools=True,
                        local=True,
                    ),
                ),
            ),
        ]

    # Choice 1 is qwen3:8b under Ollama; then Enter to skip Tavily
    answers = iter(["1", ""])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=mock_detector,
    )

    assert ok is True
    text = console.export_text()
    assert "Found models running on this computer:" in text
    assert "Ollama" in text
    assert "LM Studio" in text
    assert "qwen3:8b" in text
    assert "can use tools" in text
    assert "32k context" in text
    assert "Default model: ollama/qwen3:8b (change any time with /model)" in text

    # Verify no secret file was written and model is configured
    assert not (aldwin_home / "secrets.yaml").exists()
    status = get_onboarding_status(aldwin_home)
    assert status.model == "ollama/qwen3:8b"


@pytest.mark.uninitialized_home
async def test_path_a_retains_cached_models_on_transient_error(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path A retains models when discovery reports running=False with cached models."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def transient_error_detector(
        *_args: Any, **_kwargs: Any
    ) -> list[LocalProviderStatus]:
        return [
            LocalProviderStatus(
                name="ollama",
                title="Ollama",
                base_url="http://127.0.0.1:11434",
                running=False,
                models=(
                    ModelInfo(
                        id="qwen3:8b",
                        title="qwen3:8b",
                        context_window=32768,
                        supports_tools=True,
                        local=True,
                    ),
                ),
            )
        ]

    answers = iter(["1", ""])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=transient_error_detector,
    )
    assert ok is True
    assert "qwen3:8b" in console.export_text()
    assert get_onboarding_status(aldwin_home).model == "ollama/qwen3:8b"


@pytest.mark.uninitialized_home
async def test_path_b_free_model_instructions_exit(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path B choosing 'Run a free model' prints 3 steps and exits."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def empty_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return []

    # Choice 2 is "Run a free model on this computer"
    answers = iter(["2"])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=empty_detector,
    )

    assert ok is False
    text = console.export_text()
    assert "No model is running on this computer yet" in text
    assert "https://ollama.com/download" in text
    assert "ollama pull qwen3:8b" in text
    assert "Run aldwin again" in text
    assert "Aldwin will detect it automatically" in text


@pytest.mark.uninitialized_home
async def test_path_c_upgrade_skips_picker(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing 0.0.3 config with saved OpenAI key skips onboarding."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    bootstrap(plain=True)
    from aldwin.client import save_setup

    save_setup(openai_api_key="sk-existing-key-12345", model="gpt-4o", home=aldwin_home)

    existing_boot = BootstrapResult(
        home=aldwin_home,
        display_home=str(aldwin_home),
        created=False,
        model="gpt-4o",
        plain=True,
    )

    assert needs_onboarding(existing_boot) is False


@pytest.mark.uninitialized_home
async def test_chat_only_warning_rejected_reprompts_picker(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejecting a chat-only model returns to the picker instead of exiting."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def mock_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return [
            LocalProviderStatus(
                name="ollama",
                title="Ollama",
                base_url="http://127.0.0.1:11434",
                running=True,
                models=(
                    ModelInfo(
                        id="gemma3:4b",
                        title="gemma3:4b",
                        context_window=131072,
                        supports_tools=False,
                        local=True,
                    ),
                    ModelInfo(
                        id="qwen3:8b",
                        title="qwen3:8b",
                        context_window=32768,
                        supports_tools=True,
                        local=True,
                    ),
                ),
            )
        ]

    # Pick 1 (gemma3:4b) -> reject with 'n' -> picker re-prompts!
    # Now pick 2 (qwen3:8b) -> skip Tavily with ""
    answers = iter(["1", "n", "2", ""])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=mock_detector,
    )

    assert ok is True
    text = console.export_text()
    assert "gemma3:4b doesn't support tools" in text
    assert "Default model: ollama/qwen3:8b" in text
    assert get_onboarding_status(aldwin_home).model == "ollama/qwen3:8b"


@pytest.mark.uninitialized_home
async def test_chat_only_warning_confirmed(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chat-only warning confirmed by user proceeds to save model."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def mock_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return [
            LocalProviderStatus(
                name="ollama",
                title="Ollama",
                base_url="http://127.0.0.1:11434",
                running=True,
                models=(
                    ModelInfo(
                        id="gemma3:4b",
                        title="gemma3:4b",
                        context_window=131072,
                        supports_tools=False,
                        local=True,
                    ),
                ),
            )
        ]

    # Pick 1 (gemma3:4b), confirm with 'y', skip Tavily with ""
    answers = iter(["1", "y", ""])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=mock_detector,
    )

    assert ok is True
    text = console.export_text()
    assert "gemma3:4b doesn't support tools" in text
    assert "Default model: ollama/gemma3:4b" in text
    assert get_onboarding_status(aldwin_home).model == "ollama/gemma3:4b"


@pytest.mark.uninitialized_home
async def test_path_a_switch_to_cloud_flow(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In Path A, selecting cloud provider option transitions to Cloud flow."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def mock_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return [
            LocalProviderStatus(
                name="ollama",
                title="Ollama",
                base_url="http://127.0.0.1:11434",
                running=True,
                models=(
                    ModelInfo(
                        id="qwen3:8b",
                        title="qwen3:8b",
                        context_window=32768,
                        supports_tools=True,
                        local=True,
                    ),
                ),
            )
        ]

    # Item 1 is qwen3:8b, Item 2 is "Or use a cloud provider instead"
    # Choose 2 (Cloud), then 1 (OpenAI), then key, then model 1, then skip Tavily
    answers = iter(["2", "1", "sk-openai-cloud-key-valid", "1", ""])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    async def fake_checker(_key: str) -> KeyCheckResult:
        return "ok"

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        key_checker=fake_checker,
        local_detector=mock_detector,
    )

    assert ok is True
    text = console.export_text()
    assert "Which provider?" in text
    assert "Checking key… sk-…alid ✓" in text
    assert "Default model: openai/gpt-4o" in text


@pytest.mark.uninitialized_home
async def test_path_b_cloud_provider_flow(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path B selecting Cloud provider verifies key and persists secrets."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def empty_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return []

    # Mock save_provider_key for anthropic
    async def mock_save_key(provider: str, key: str, **_kwargs: Any) -> AccessStatus:
        if key == "sk-ant-valid-1234":
            return AccessStatus.OK
        return AccessStatus.INVALID_KEY

    monkeypatch.setattr("aldwin.cli.onboarding.save_provider_key", mock_save_key)

    async def mock_list_models(provider: str, **_kwargs: Any) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="claude-sonnet-4-5",
                title="Claude 3.5 Sonnet",
                context_window=200000,
                supports_tools=True,
                local=False,
            )
        ]

    monkeypatch.setattr("aldwin.cli.onboarding.list_models_for", mock_list_models)

    # 1: Use cloud provider, 2: Anthropic, key, 1: claude-sonnet-4-5, "": skip Tavily
    answers = iter(["1", "2", "sk-ant-valid-1234", "1", ""])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=empty_detector,
    )

    assert ok is True
    text = console.export_text()
    assert "Which provider?" in text
    assert "Checking key… sk-…1234 ✓" in text
    assert "Default model: anthropic/claude-sonnet-4-5" in text


@pytest.mark.uninitialized_home
async def test_setup_menu_options(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """aldwin setup menu offers Local, Cloud, and Keep current."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    # Preconfigure model
    from aldwin.client import save_setup

    save_setup(model="openai/gpt-4o", home=aldwin_home)

    # Option 3: Keep current (openai/gpt-4o), Enter for Tavily
    answers = iter(["3", ""])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_setup(
        boot,
        console,
        prompt_fn=fake_prompt,
    )
    assert ok is True
    text = console.export_text()
    assert "How would you like to configure Aldwin?" in text
    assert "Local model (runs on this computer)" in text
    assert "Cloud provider (OpenAI, Anthropic, Gemini and others)" in text
    assert "Keep current (openai/gpt-4o)" in text
    assert "Saved setup in" in text
    assert get_onboarding_status(aldwin_home).model == "openai/gpt-4o"


@pytest.mark.uninitialized_home
async def test_setup_local_model_selection(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup choice 1 (Local model) scans local models and updates model."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def mock_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return [
            LocalProviderStatus(
                name="lmstudio",
                title="LM Studio",
                base_url="http://127.0.0.1:1234/v1",
                running=True,
                models=(
                    ModelInfo(
                        id="qwen2.5-coder",
                        title="qwen2.5-coder",
                        context_window=32768,
                        supports_tools=True,
                        local=True,
                    ),
                ),
            )
        ]

    # 1: Local model, 1: qwen2.5-coder, "": skip Tavily
    answers = iter(["1", "1", ""])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_setup(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=mock_detector,
    )

    assert ok is True
    assert "default model: lmstudio/qwen2.5-coder" in console.export_text()
    assert get_onboarding_status(aldwin_home).model == "lmstudio/qwen2.5-coder"


@pytest.mark.uninitialized_home
async def test_setup_local_model_empty_shows_instructions(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup choice 1 with no local models shows installation instructions."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def empty_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return []

    # 1: Local model
    answers = iter(["1"])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_setup(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=empty_detector,
    )

    assert ok is False
    assert "To run a free model on this computer:" in console.export_text()


@pytest.mark.uninitialized_home
async def test_onboarding_cancellation_and_eof(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KeyboardInterrupt/EOFError during onboarding returns False cleanly."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def mock_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return []

    async def interrupt_prompt(_label: str, _is_password: bool) -> str:
        raise KeyboardInterrupt

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=interrupt_prompt,
        local_detector=mock_detector,
    )
    assert ok is False


@pytest.mark.uninitialized_home
async def test_provider_key_error_branches(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider key check failure messages and retries."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def empty_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return []

    async def mock_failing_save_key(
        provider: str, key: str, **_kwargs: Any
    ) -> AccessStatus:
        return AccessStatus.INVALID_KEY

    monkeypatch.setattr(
        "aldwin.cli.onboarding.save_provider_key", mock_failing_save_key
    )

    # 1: Cloud, 2: Anthropic, then 3 rejected keys
    answers = iter(["1", "2", "bad-key-1", "bad-key-2", "bad-key-3"])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=empty_detector,
    )
    assert ok is False
    text = console.export_text()
    assert "That key was rejected by Anthropic." in text
    assert "Could not verify a key for Anthropic after 3 attempts." in text


@pytest.mark.uninitialized_home
async def test_onboarding_non_plain_spinner_and_default_enter(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-plain interactive console exercises Status spinner and default Enter."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = BootstrapResult(
        home=aldwin_home,
        display_home=str(aldwin_home),
        created=True,
        model="gpt-4o",
        plain=False,
    )

    async def mock_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return [
            LocalProviderStatus(
                name="ollama",
                title="Ollama",
                base_url="http://127.0.0.1:11434",
                running=True,
                models=(
                    ModelInfo(
                        id="qwen3:8b",
                        title="qwen3:8b",
                        context_window=32768,
                        supports_tools=True,
                        local=True,
                    ),
                ),
            )
        ]

    # Press Enter (picks default 0: qwen3:8b), then press Enter (skip Tavily)
    answers = iter(["", ""])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=False, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=mock_detector,
    )

    assert ok is True
    assert "Default model: ollama/qwen3:8b" in console.export_text()


@pytest.mark.uninitialized_home
async def test_onboarding_tavily_key_entered(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Providing a Tavily key during Path A onboarding saves it to secrets.yaml."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def mock_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return [
            LocalProviderStatus(
                name="ollama",
                title="Ollama",
                base_url="http://127.0.0.1:11434",
                running=True,
                models=(
                    ModelInfo(
                        id="qwen3:8b",
                        title="qwen3:8b",
                        context_window=32768,
                        supports_tools=True,
                        local=True,
                    ),
                ),
            )
        ]

    # Model 1, then enter Tavily key
    answers = iter(["1", "tvly-valid-test-key"])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=mock_detector,
    )
    assert ok is True
    assert "Tavily key… tvly-…-key ✓" in console.export_text()
    status = get_onboarding_status(aldwin_home)
    assert status.has_tavily_key is True
    assert status.masked_tavily_key == "tvly-…-key"


@pytest.mark.uninitialized_home
async def test_choose_option_helpers_and_blank_keys(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Option chooser string matching, out-of-range recovery, and blank key prompts."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def empty_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return []

    async def mock_save_key(provider: str, key: str, **_kwargs: Any) -> AccessStatus:
        return AccessStatus.OK if key == "sk-or-valid" else AccessStatus.INVALID_KEY

    monkeypatch.setattr("aldwin.cli.onboarding.save_provider_key", mock_save_key)

    # 1: Cloud, openrouter, blank key, valid key, skip Tavily
    answers = iter(["1", "openrouter", "", "sk-or-valid", ""])

    async def fake_prompt(_label: str, _is_password: bool) -> str:
        return next(answers)

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        local_detector=empty_detector,
    )
    assert ok is True
    text = console.export_text()
    assert "Key required for OpenRouter" in text
    assert "Default model: openrouter/default" in text


@pytest.mark.uninitialized_home
async def test_setup_chat_only_warning_and_cancellation(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup choice 1 with chat-only model prompts warning and can cancel."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    async def mock_detector(*_args: Any, **_kwargs: Any) -> list[LocalProviderStatus]:
        return [
            LocalProviderStatus(
                name="ollama",
                title="Ollama",
                base_url="http://127.0.0.1:11434",
                running=True,
                models=(
                    ModelInfo(
                        id="chat-only-4b",
                        title="chat-only-4b",
                        context_window=8192,
                        supports_tools=False,
                        local=True,
                    ),
                ),
            )
        ]

    # Setup option 1 (Local), model 1, confirm 'y', skip Tavily
    answers_confirm = iter(["1", "1", "y", ""])

    async def prompt_confirm(_label: str, _is_password: bool) -> str:
        return next(answers_confirm)

    buf1 = io.StringIO()
    console1 = create_console(plain=True, width=80, record=True, file=buf1)
    ok1 = await run_setup(
        boot,
        console1,
        prompt_fn=prompt_confirm,
        local_detector=mock_detector,
    )
    assert ok1 is True
    assert "default model: ollama/chat-only-4b" in console1.export_text()

    # Now test reject: option 1 (Local), model 1, reject 'n', then cancel
    answers_reject = iter(["1", "1", "n", "cancel"])

    async def prompt_reject(_label: str, _is_password: bool) -> str:
        return next(answers_reject)

    buf2 = io.StringIO()
    console2 = create_console(plain=True, width=80, record=True, file=buf2)
    ok2 = await run_setup(
        boot,
        console2,
        prompt_fn=prompt_reject,
        local_detector=mock_detector,
    )
    assert ok2 is False
    assert "Cancelled model selection." in console2.export_text()

    # Test top-level cancellation on setup
    async def cancel_prompt(_label: str, _is_password: bool) -> str:
        raise KeyboardInterrupt

    buf3 = io.StringIO()
    console3 = create_console(plain=True, width=80, record=True, file=buf3)
    ok3 = await run_setup(
        boot,
        console3,
        prompt_fn=cancel_prompt,
    )
    assert ok3 is False
