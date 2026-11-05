"""First-run onboarding and interactive `aldwin setup` workflow."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Final

from rich.console import Console
from rich.status import Status
from rich.text import Text

from aldwin.cli.render import ChatRenderer
from aldwin.cli.secret_prompt import prompt_input as _default_prompt
from aldwin.cli.theme import filter_canonical_models
from aldwin.client import (
    AccessStatus,
    BootstrapResult,
    KeyCheckResult,
    LocalProviderStatus,
    ModelInfo,
    check_openai_key,
    detect_local_providers,
    get_onboarding_status,
    list_available_models,
    list_models_for,
    mask_secret,
    save_provider_key,
    save_setup,
)

MAX_KEY_ATTEMPTS: Final[int] = 3

PromptCallable = Callable[[str, bool], Awaitable[str]]
KeyCheckerCallable = Callable[[str], Awaitable[KeyCheckResult]]
ModelListerCallable = Callable[[str | None, Path | None], Awaitable[list[str]]]
LocalDetectorCallable = Callable[..., Awaitable[list[LocalProviderStatus]]]

_KEY_ERROR_MESSAGES: Final[dict[KeyCheckResult, str]] = {
    "invalid": "That key was rejected by OpenAI. Check it and paste it again.",
    "no_access": (
        "That key doesn't have access to OpenAI chat models. "
        "Check your account billing and permissions."
    ),
    "network_error": (
        "Can't reach OpenAI to verify your key. Check your connection and try again."
    ),
}

_FIRST_SHIP_CLOUD_PROVIDERS: Final[tuple[tuple[str, str, str], ...]] = (
    ("openai", "OpenAI", "platform.openai.com/api-keys"),
    ("anthropic", "Anthropic", "console.anthropic.com"),
    ("gemini", "Google Gemini", "aistudio.google.com/apikey"),
    ("deepseek", "DeepSeek", "platform.deepseek.com"),
    ("openrouter", "OpenRouter", "openrouter.ai/keys"),
)


def needs_onboarding(boot: BootstrapResult) -> bool:
    """Return True when onboarding is needed on fresh install without keys."""
    status = get_onboarding_status(boot.home)
    # Existing install with configured provider/key: skip onboarding
    if not boot.created:
        if status.has_openai_key or status.has_provider_key or status.is_local:
            return False
        return True

    # Fresh install: if OpenAI key is in env, onboarding validates it without picker
    if status.has_openai_key and status.openai_source == "env":
        return True
    return not (status.has_openai_key or status.has_provider_key or status.is_local)


def _format_context(tokens: int) -> str:
    """Format token count into readable context string, e.g. 32k context."""
    if tokens >= 1024:
        return f"{tokens // 1024}k context"
    return f"{tokens} context"


def _format_tools_capability(supports_tools: bool | None) -> str:
    """Format tool capability label."""
    if supports_tools is True:
        return "can use tools"
    if supports_tools is False:
        return "chat only"
    return ""


async def _verify_key_with_spinner(
    api_key: str,
    *,
    console: Console,
    plain: bool,
    checker: KeyCheckerCallable,
) -> KeyCheckResult:
    """Run `checker(api_key)` while displaying `Checking key…`."""
    if plain:
        return await checker(api_key)
    with Status("Checking key…", console=console, spinner="dots"):
        return await checker(api_key)


async def _choose_option(
    items: Sequence[str],
    *,
    header: str | None = None,
    default_idx: int = 0,
    console: Console,
    plain: bool,
    prompt_fn: PromptCallable,
) -> int | None:
    """Prompt user to choose from a list of options."""
    if prompt_fn is _default_prompt and console.is_interactive and not plain:
        from aldwin.cli.picker import pick_index

        return await pick_index(
            items,
            header=header,
            default=default_idx,
            plain=plain,
        )

    if header:
        console.print(Text(header, style="accent"))
    for idx, item in enumerate(items, start=1):
        console.print(Text(f"  {idx}. {item}"))

    try:
        raw = await prompt_fn(
            f"Choice [1-{len(items)}, Enter for {default_idx + 1}]: ",
            False,
        )
    except (EOFError, KeyboardInterrupt):
        return None

    cleaned = raw.strip()
    if not cleaned:
        return default_idx
    if cleaned.lower() in ("cancel", "exit", "q", "quit"):
        return None
    if cleaned.isdigit():
        val = int(cleaned)
        if 1 <= val <= len(items):
            return val - 1
    for idx, item in enumerate(items):
        if cleaned.lower() in item.lower():
            return idx
    return default_idx


async def _prompt_and_verify_openai_key(
    *,
    console: Console,
    plain: bool,
    prompt_fn: PromptCallable,
    checker: KeyCheckerCallable,
    prompt_label: str = "OpenAI API key (input hidden): ",
    allow_empty_keep: bool = False,
) -> tuple[bool, str | None]:
    """Prompt up to 3 times for an OpenAI API key and validate it."""
    for _attempt in range(1, MAX_KEY_ATTEMPTS + 1):
        try:
            raw = await prompt_fn(prompt_label, True)
        except (EOFError, KeyboardInterrupt):
            console.print(
                Text(
                    "Setup cancelled. Run `aldwin setup` when you are ready.",
                    style="warn",
                )
            )
            return False, None

        cleaned = raw.strip()
        if not cleaned:
            if allow_empty_keep:
                return True, None
            console.print(Text(_KEY_ERROR_MESSAGES["invalid"], style="err"))
            continue

        result = await _verify_key_with_spinner(
            cleaned,
            console=console,
            plain=plain,
            checker=checker,
        )
        if result == "ok":
            masked = mask_secret(cleaned)
            ok_line = Text("Checking key… ", style="muted")
            if masked:
                ok_line.append(f"{masked} ", style="muted")
            ok_line.append("✓", style="ok")
            console.print(ok_line)
            return True, cleaned

        err_msg = _KEY_ERROR_MESSAGES.get(result, _KEY_ERROR_MESSAGES["invalid"])
        console.print(Text(err_msg, style="err"))

    console.print(
        Text(
            "Could not verify an OpenAI key after 3 attempts. "
            "Run `aldwin setup` to try again.",
            style="err",
        )
    )
    return False, None


async def _prompt_and_verify_provider_key(
    provider: str,
    title: str,
    key_hint: str,
    *,
    home: Path | None,
    console: Console,
    prompt_fn: PromptCallable,
) -> tuple[bool, str | None]:
    """Prompt up to 3 times for a provider key and validate via save_provider_key."""
    for _attempt in range(1, MAX_KEY_ATTEMPTS + 1):
        try:
            raw = await prompt_fn(
                f"{title} key (get one at {key_hint}, input hidden): ",
                True,
            )
        except (EOFError, KeyboardInterrupt):
            console.print(
                Text(
                    "Setup cancelled. Run `aldwin setup` when you are ready.",
                    style="warn",
                )
            )
            return False, None

        cleaned = raw.strip()
        if not cleaned:
            console.print(
                Text(
                    f"Key required for {title}. Please paste a valid key.",
                    style="err",
                )
            )
            continue

        try:
            status = await save_provider_key(provider, cleaned, home=home)
        except Exception as exc:
            console.print(Text(f"Failed to verify key: {exc}", style="err"))
            continue

        if status == AccessStatus.OK:
            masked = mask_secret(cleaned)
            ok_line = Text("Checking key… ", style="muted")
            if masked:
                ok_line.append(f"{masked} ", style="muted")
            ok_line.append("✓", style="ok")
            console.print(ok_line)
            return True, cleaned

        console.print(
            Text(
                f"That key was rejected by {title}. Check it and paste it again.",
                style="err",
            )
        )

    console.print(
        Text(
            f"Could not verify a key for {title} after 3 attempts.",
            style="err",
        )
    )
    return False, None


async def _confirm_chat_only_model(
    model_id: str,
    prompt_fn: PromptCallable,
    console: Console,
) -> bool:
    """Prompt user to confirm selection of a model that lacks tool support."""
    warn_text = (
        f"{model_id} doesn't support tools, so Aldwin can chat but can't "
        "read files, search or act. Use this model anyway?"
    )
    console.print(Text(warn_text, style="warn"))
    try:
        reply = await prompt_fn("Confirm [y/N]: ", False)
    except (EOFError, KeyboardInterrupt):
        return False
    confirmed = reply.strip().lower() == "y"
    if not confirmed:
        console.print(Text("Cancelled model selection.", style="muted"))
    return confirmed


async def _prompt_optional_tavily(
    prompt_fn: PromptCallable,
    console: Console,
) -> str | None:
    """Prompt for optional Tavily web search key."""
    try:
        tavily_raw = await prompt_fn(
            "Tavily key for web search (input hidden, Enter to skip): ",
            True,
        )
    except (EOFError, KeyboardInterrupt):
        tavily_raw = ""
    tavily_key = tavily_raw.strip() or None
    if tavily_key:
        tavily_line = Text(f"Tavily key… {mask_secret(tavily_key)} ", style="muted")
        tavily_line.append("✓", style="ok")
        console.print(tavily_line)
    return tavily_key


async def _handle_path_b_free_model(console: Console) -> bool:
    """Display instructions for installing a free local model and exit."""
    console.print()
    console.print(Text("To run a free model on this computer:", style="accent"))
    console.print(Text("  1. Install Ollama from https://ollama.com/download"))
    console.print(Text("  2. In your terminal, run: ollama pull qwen3:8b"))
    console.print(Text("  3. Run aldwin again"))
    console.print()
    console.print(Text("Aldwin will detect it automatically.", style="muted"))
    console.print()
    return False


async def _handle_path_b_cloud_provider(
    boot: BootstrapResult,
    console: Console,
    prompt_fn: PromptCallable,
    key_checker: KeyCheckerCallable | None = None,
    model_lister: ModelListerCallable | None = None,
    allow_empty_keep: bool = False,
) -> bool:
    """Run cloud provider selection, key verification, and model selection."""
    provider_labels = [
        f"{title} ({hint})" for _, title, hint in _FIRST_SHIP_CLOUD_PROVIDERS
    ]
    chosen_idx = await _choose_option(
        provider_labels,
        header="Which provider?",
        default_idx=0,
        console=console,
        plain=boot.plain,
        prompt_fn=prompt_fn,
    )
    if chosen_idx is None:
        return False

    prov_id, prov_title, prov_hint = _FIRST_SHIP_CLOUD_PROVIDERS[chosen_idx]

    if prov_id == "openai":
        status = get_onboarding_status(boot.home)
        active_checker = key_checker or check_openai_key
        has_key = status.has_openai_key and bool(status.masked_openai_key)
        prompt_label = (
            f"OpenAI API key [{status.masked_openai_key}] "
            "(input hidden, Enter to keep): "
            if allow_empty_keep and has_key
            else "OpenAI API key (input hidden): "
        )
        ok, verified_key = await _prompt_and_verify_openai_key(
            console=console,
            plain=boot.plain,
            prompt_fn=prompt_fn,
            checker=active_checker,
            prompt_label=prompt_label,
            allow_empty_keep=allow_empty_keep and has_key,
        )
        if not ok:
            return False

        active_lister = model_lister or list_available_models
        all_models = await active_lister(verified_key, boot.home)
        if not all_models:
            all_models = [boot.model]
        canonical, _ = filter_canonical_models(all_models, boot.model)
        m_idx = await _choose_option(
            canonical,
            header="Available OpenAI chat models:",
            default_idx=0,
            console=console,
            plain=boot.plain,
            prompt_fn=prompt_fn,
        )
        chosen_m_name = canonical[m_idx] if m_idx is not None else boot.model
        model_ref = chosen_m_name if "/" in chosen_m_name else f"openai/{chosen_m_name}"
    else:
        ok, verified_key = await _prompt_and_verify_provider_key(
            prov_id,
            prov_title,
            prov_hint,
            home=boot.home,
            console=console,
            prompt_fn=prompt_fn,
        )
        if not ok or verified_key is None:
            return False

        models = await list_models_for(prov_id, home=boot.home)
        if models:
            model_labels = [
                f"{m.id:<25} {_format_tools_capability(m.supports_tools):<15} "
                f"{_format_context(m.context_window)}"
                for m in models
            ]
            m_idx = await _choose_option(
                model_labels,
                header=f"Available {prov_title} models:",
                default_idx=0,
                console=console,
                plain=boot.plain,
                prompt_fn=prompt_fn,
            )
            if m_idx is None:
                return False
            chosen_m = models[m_idx]
            if chosen_m.supports_tools is False:
                confirmed = await _confirm_chat_only_model(
                    chosen_m.id, prompt_fn, console
                )
                if not confirmed:
                    return False
            model_ref = f"{prov_id}/{chosen_m.id}"
        else:
            model_ref = f"{prov_id}/default"

    tavily_key = await _prompt_optional_tavily(prompt_fn, console)
    save_setup(
        openai_api_key=verified_key if prov_id == "openai" else None,
        tavily_api_key=tavily_key,
        model=model_ref,
        home=boot.home,
    )
    if prov_id == "openai" and verified_key and os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = verified_key

    console.print(
        Text(f"Default model: {model_ref} (change any time with /model)", style="muted")
    )
    if boot.created:
        console.print(
            Text(f"Created {boot.display_home}", style="muted"), soft_wrap=True
        )
    console.print()
    return True


async def run_onboarding(
    boot: BootstrapResult,
    console: Console,
    *,
    prompt_fn: PromptCallable | None = None,
    key_checker: KeyCheckerCallable | None = None,
    local_detector: LocalDetectorCallable | None = None,
) -> bool:
    """Run the first-run onboarding wizard.

    Path C: Skipped automatically if valid OPENAI_API_KEY is in environment.
    Path A: If local models running, list and allow zero-key setup.
    Path B: If no local models, offer cloud setup or instructions then exit.
    """
    active_prompt = prompt_fn or _default_prompt
    active_checker = key_checker or check_openai_key
    active_detector = local_detector or detect_local_providers

    # Path C: Valid OPENAI_API_KEY in environment auto-keeps OpenAI default
    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        status_check = await _verify_key_with_spinner(
            env_key,
            console=console,
            plain=boot.plain,
            checker=active_checker,
        )
        if status_check == "ok":
            if boot.created:
                console.print(
                    Text(f"Created {boot.display_home}", style="muted"),
                    soft_wrap=True,
                )
            return True
        err_msg = _KEY_ERROR_MESSAGES.get(status_check, _KEY_ERROR_MESSAGES["invalid"])
        console.print(
            Text(f"OPENAI_API_KEY in environment failed: {err_msg}", style="err")
        )

    from aldwin.cli.brand import (
        ALDWIN_WORDMARK,
        ALDWIN_WORDMARK_ONE_LINE,
        PRIMER_LINES,
        TAGLINE,
    )
    from aldwin.cli.theme import should_show_wordmark_splash

    width = console.width or 80
    show_art = should_show_wordmark_splash(plain=boot.plain, width=width)
    if boot.plain or not console.is_interactive or not show_art:
        console.print(Text(ALDWIN_WORDMARK_ONE_LINE, style="accent"))
        console.print(Text(TAGLINE, style="muted"))
    else:
        for line in ALDWIN_WORDMARK:
            console.print(Text(line, style="accent"))
        console.print(Text(TAGLINE, style="muted"))
    console.print()
    for line in PRIMER_LINES:
        console.print(Text(line, style="fg"))
    console.print()

    # Probe for local models (<= 1s timeout)
    try:
        if boot.plain:
            local_statuses = await active_detector(timeout=1.0)
        else:
            with Status("Looking for local models…", console=console, spinner="dots"):
                local_statuses = await active_detector(timeout=1.0)
    except Exception as exc:
        logger_error = Text(f"Failed to detect local providers: {exc}", style="muted")
        console.print(logger_error)
        local_statuses = []

    running_locals = [s for s in local_statuses if s.models]

    if running_locals:
        # Path A: Local models found!
        model_entries: list[tuple[str, ModelInfo]] = []
        labels: list[str] = []
        for provider_status in running_locals:
            console.print(Text(f"  {provider_status.title}", style="bold"))
            for m in provider_status.models:
                model_entries.append((provider_status.name, m))
                cap = _format_tools_capability(m.supports_tools)
                ctx = _format_context(m.context_window)
                labels.append(f"[{provider_status.title}] {m.id:<18} {cap:<15} {ctx}")

        labels.append("Or use a cloud provider instead")

        while True:
            chosen_idx = await _choose_option(
                labels,
                header="Found models running on this computer:",
                default_idx=0,
                console=console,
                plain=boot.plain,
                prompt_fn=active_prompt,
            )

            if chosen_idx is None:
                return False

            if chosen_idx >= len(model_entries):
                # User selected "Or use a cloud provider instead"
                break

            chosen_provider, chosen_model = model_entries[chosen_idx]

            # Chat-only warning check
            if chosen_model.supports_tools is False:
                confirmed = await _confirm_chat_only_model(
                    chosen_model.id, active_prompt, console
                )
                if not confirmed:
                    continue

            tavily_key = await _prompt_optional_tavily(active_prompt, console)
            model_ref = f"{chosen_provider}/{chosen_model.id}"
            save_setup(
                model=model_ref,
                tavily_api_key=tavily_key,
                home=boot.home,
            )

            console.print(
                Text(
                    f"Default model: {model_ref} (change any time with /model)",
                    style="muted",
                )
            )
            if boot.created:
                console.print(
                    Text(f"Created {boot.display_home}", style="muted"),
                    soft_wrap=True,
                )
            console.print()
            return True
        # User selected "Or use a cloud provider instead", fall through to cloud flow

    # Path B: No local model found (or cloud selected)
    if not running_locals:
        path_b_options = [
            "Use a cloud provider (OpenAI, Anthropic, Gemini and others)",
            "Run a free model on this computer (shows you how)",
        ]
        choice_idx = await _choose_option(
            path_b_options,
            header=(
                "No model is running on this computer yet. How would you like to start?"
            ),
            default_idx=0,
            console=console,
            plain=boot.plain,
            prompt_fn=active_prompt,
        )
        if choice_idx == 1:
            return await _handle_path_b_free_model(console)
        if choice_idx is None:
            return False

    return await _handle_path_b_cloud_provider(
        boot,
        console,
        active_prompt,
        key_checker=active_checker,
    )


async def run_setup(
    boot: BootstrapResult,
    console: Console,
    *,
    prompt_fn: PromptCallable | None = None,
    key_checker: KeyCheckerCallable | None = None,
    model_lister: ModelListerCallable | None = None,
    local_detector: LocalDetectorCallable | None = None,
) -> bool:
    """Run interactive `aldwin setup` to configure keys and default model."""
    active_prompt = prompt_fn or _default_prompt
    active_detector = local_detector or detect_local_providers

    status = get_onboarding_status(boot.home)
    renderer = ChatRenderer(console, plain=boot.plain)
    renderer.render_welcome(
        first_run=True,
        model=status.model,
        display_home=boot.display_home,
    )
    console.print()

    setup_choices = [
        "Local model (runs on this computer)",
        "Cloud provider (OpenAI, Anthropic, Gemini and others)",
        f"Keep current ({status.model})",
    ]

    choice = await _choose_option(
        setup_choices,
        header="How would you like to configure Aldwin?",
        default_idx=2,
        console=console,
        plain=boot.plain,
        prompt_fn=active_prompt,
    )

    if choice is None:
        return False

    if choice == 0:
        # Local model branch
        try:
            if boot.plain:
                local_statuses = await active_detector(timeout=1.0)
            else:
                with Status(
                    "Looking for local models…",
                    console=console,
                    spinner="dots",
                ):
                    local_statuses = await active_detector(timeout=1.0)
        except Exception as exc:
            msg = f"Failed to detect local providers: {exc}"
            console.print(Text(msg, style="muted"))
            local_statuses = []

        running_locals = [s for s in local_statuses if s.models]
        if not running_locals:
            return await _handle_path_b_free_model(console)

        model_entries: list[tuple[str, ModelInfo]] = []
        labels: list[str] = []
        for provider_status in running_locals:
            console.print(Text(f"  {provider_status.title}", style="bold"))
            for m in provider_status.models:
                model_entries.append((provider_status.name, m))
                cap = _format_tools_capability(m.supports_tools)
                ctx = _format_context(m.context_window)
                labels.append(f"[{provider_status.title}] {m.id:<18} {cap:<15} {ctx}")

        while True:
            chosen_idx = await _choose_option(
                labels,
                header="Found models running on this computer:",
                default_idx=0,
                console=console,
                plain=boot.plain,
                prompt_fn=active_prompt,
            )
            if chosen_idx is None:
                return False

            chosen_provider, chosen_model = model_entries[chosen_idx]
            if chosen_model.supports_tools is False:
                confirmed = await _confirm_chat_only_model(
                    chosen_model.id, active_prompt, console
                )
                if not confirmed:
                    continue

            tavily_key = await _prompt_optional_tavily(active_prompt, console)
            model_ref = f"{chosen_provider}/{chosen_model.id}"
            save_setup(model=model_ref, tavily_api_key=tavily_key, home=boot.home)
            console.print(
                Text(
                    f"Saved setup in {boot.display_home} (default model: {model_ref}).",
                    style="ok",
                ),
                soft_wrap=True,
            )
            return True

    if choice == 1:
        # Cloud provider branch
        return await _handle_path_b_cloud_provider(
            boot,
            console,
            active_prompt,
            key_checker=key_checker,
            model_lister=model_lister,
            allow_empty_keep=True,
        )

    # Keep current branch
    tavily_label = (
        f"Tavily key for web search [{status.masked_tavily_key}] (Enter to keep): "
        if status.has_tavily_key
        else "Tavily key for web search (input hidden, Enter to skip): "
    )
    try:
        tavily_raw = await active_prompt(tavily_label, True)
    except (EOFError, KeyboardInterrupt):
        tavily_raw = ""
    new_tavily_key = tavily_raw.strip() or None

    save_setup(
        tavily_api_key=new_tavily_key,
        model=status.model,
        home=boot.home,
    )
    console.print(
        Text(
            f"Saved setup in {boot.display_home} (default model: {status.model}).",
            style="ok",
        ),
        soft_wrap=True,
    )
    return True
