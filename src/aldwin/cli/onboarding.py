"""First-run onboarding and interactive `aldwin setup` workflow."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final

from rich.console import Console
from rich.status import Status
from rich.text import Text

from aldwin.cli.render import ChatRenderer
from aldwin.cli.secret_prompt import prompt_input as _default_prompt
from aldwin.cli.theme import filter_canonical_models
from aldwin.client import (
    BootstrapResult,
    KeyCheckResult,
    check_openai_key,
    get_onboarding_status,
    list_available_models,
    mask_secret,
    save_setup,
)

MAX_KEY_ATTEMPTS: Final[int] = 3

PromptCallable = Callable[[str, bool], Awaitable[str]]
KeyCheckerCallable = Callable[[str], Awaitable[KeyCheckResult]]
ModelListerCallable = Callable[[str | None, Path | None], Awaitable[list[str]]]

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


def needs_onboarding(boot: BootstrapResult) -> bool:
    """Return `True` when no OpenAI key is configured or on first run with env key."""
    status = get_onboarding_status(boot.home)
    if not status.has_openai_key:
        return True
    return boot.created and status.openai_source == "env"


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


async def _prompt_and_verify_openai_key(
    *,
    console: Console,
    plain: bool,
    prompt_fn: PromptCallable,
    checker: KeyCheckerCallable,
    prompt_label: str = "OpenAI API key (input hidden): ",
    allow_empty_keep: bool = False,
) -> tuple[bool, str | None]:
    """Prompt up to 3 times for an OpenAI API key and validate it.

    Returns `(succeeded, verified_key_or_none_if_kept)`.
    """
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


async def run_onboarding(
    boot: BootstrapResult,
    console: Console,
    *,
    prompt_fn: PromptCallable | None = None,
    key_checker: KeyCheckerCallable | None = None,
) -> bool:
    """Run the first-run onboarding wizard.

    Skipped automatically if `OPENAI_API_KEY` is set in the environment and valid.
    Returns `True` when onboarding completes (or is skipped), or `False` after
    3 failed key attempts.
    """
    active_prompt = prompt_fn or _default_prompt
    active_checker = key_checker or check_openai_key

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

    # Short wordmark + one-screen primer, then minimal key setup.
    # Timed column reveal is skipped under plain / narrow / reduced-motion.
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

    ok, verified_openai_key = await _prompt_and_verify_openai_key(
        console=console,
        plain=boot.plain,
        prompt_fn=active_prompt,
        checker=active_checker,
    )
    if not ok or verified_openai_key is None:
        return False

    try:
        tavily_raw = await active_prompt(
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

    save_setup(
        openai_api_key=verified_openai_key,
        tavily_api_key=tavily_key,
        model=boot.model,
        home=boot.home,
    )
    if env_key:
        os.environ["OPENAI_API_KEY"] = verified_openai_key

    console.print(
        Text(
            f"Default model: {boot.model} (change any time with /model)",
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


def _resolve_model_choice(choice: str, models: list[str], current_model: str) -> str:
    """Resolve a 1-based index or explicit model name from `choice`."""
    cleaned = choice.strip()
    if not cleaned:
        return current_model
    if cleaned.isdigit():
        idx = int(cleaned)
        if 1 <= idx <= len(models):
            return models[idx - 1]
    if cleaned in models:
        return cleaned
    return current_model


async def run_setup(
    boot: BootstrapResult,
    console: Console,
    *,
    prompt_fn: PromptCallable | None = None,
    key_checker: KeyCheckerCallable | None = None,
    model_lister: ModelListerCallable | None = None,
) -> bool:
    """Run interactive `aldwin setup` to configure keys and default model."""
    active_prompt = prompt_fn or _default_prompt
    active_checker = key_checker or check_openai_key
    active_lister = model_lister or list_available_models

    status = get_onboarding_status(boot.home)
    renderer = ChatRenderer(console, plain=boot.plain)
    renderer.render_welcome(
        first_run=True,
        model=status.model,
        display_home=boot.display_home,
    )
    console.print()

    if status.has_openai_key:
        openai_label = (
            f"OpenAI API key [{status.masked_openai_key}] "
            "(input hidden, Enter to keep): "
        )
        ok, new_openai_key = await _prompt_and_verify_openai_key(
            console=console,
            plain=boot.plain,
            prompt_fn=active_prompt,
            checker=active_checker,
            prompt_label=openai_label,
            allow_empty_keep=True,
        )
        if not ok:
            return False
    else:
        ok, new_openai_key = await _prompt_and_verify_openai_key(
            console=console,
            plain=boot.plain,
            prompt_fn=active_prompt,
            checker=active_checker,
            prompt_label="OpenAI API key (input hidden): ",
            allow_empty_keep=False,
        )
        if not ok or new_openai_key is None:
            return False

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
    if new_tavily_key:
        tavily_line = Text(
            f"Tavily key… {mask_secret(new_tavily_key)} ",
            style="muted",
        )
        tavily_line.append("✓", style="ok")
        console.print(tavily_line)

    all_models = await active_lister(new_openai_key, boot.home)
    if not all_models:
        all_models = [status.model]
    models, _ = filter_canonical_models(all_models, status.model)

    console.print(Text("Available OpenAI chat models:", style="muted"))
    for idx, model_name in enumerate(models, start=1):
        marker = " (current)" if model_name == status.model else ""
        style = "ok" if model_name == status.model else ""
        console.print(Text(f"  {idx}. {model_name}{marker}", style=style))

    try:
        model_choice = await active_prompt(
            f"Default model [1-{len(models)}, Enter for {status.model}]: ",
            False,
        )
    except (EOFError, KeyboardInterrupt):
        model_choice = ""

    chosen_model = _resolve_model_choice(model_choice, models, status.model)
    save_setup(
        openai_api_key=new_openai_key,
        tavily_api_key=new_tavily_key,
        model=chosen_model,
        home=boot.home,
    )

    console.print(
        Text(
            f"Saved setup in {boot.display_home} (default model: {chosen_model}).",
            style="ok",
        ),
        soft_wrap=True,
    )
    return True
