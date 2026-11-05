"""Rich theme tokens, console factory, prompt_toolkit styles, and display formatters."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import IO, Final, Literal

from prompt_toolkit.styles import Style
from rich.console import Console
from rich.theme import Theme

# Lunar teal: Aldwin's own accent (cool local assistant, not Claude orange).
# TrueColor for Rich + #hex for prompt_toolkit.
ACCENT_RGB: Final[tuple[int, int, int]] = (56, 189, 176)
ACCENT_BRIGHT_RGB: Final[tuple[int, int, int]] = (122, 224, 214)
ACCENT_SHADOW_RGB: Final[tuple[int, int, int]] = (32, 120, 112)
STALL_RED_RGB: Final[tuple[int, int, int]] = (196, 74, 98)
USER_MSG_BG: Final[str] = "grey15"
ACCENT_HEX: Final[str] = "#38bdb0"

SPINNER_GLYPHS: Final[tuple[str, ...]] = ("·", "✢", "✱", "✶", "✻", "✽")
# Forward then reverse without duplicating endpoints.
SPINNER_FRAMES: Final[tuple[str, ...]] = (
    *SPINNER_GLYPHS,
    *tuple(reversed(SPINNER_GLYPHS[1:-1])),
)
GLYPH_FRAME_S: Final[float] = 1.0 / 12.0

ThemeChoice = Literal["auto", "dark", "light", "colour_blind", "plain"]

_ACCENT_STYLE: Final[str] = f"rgb({ACCENT_RGB[0]},{ACCENT_RGB[1]},{ACCENT_RGB[2]})"
_ACCENT_BRIGHT_STYLE: Final[str] = (
    f"rgb({ACCENT_BRIGHT_RGB[0]},{ACCENT_BRIGHT_RGB[1]},{ACCENT_BRIGHT_RGB[2]})"
)
_ACCENT_SHADOW_STYLE: Final[str] = (
    f"rgb({ACCENT_SHADOW_RGB[0]},{ACCENT_SHADOW_RGB[1]},{ACCENT_SHADOW_RGB[2]})"
)


def _base_theme_styles(
    *,
    accent: str,
    accent_bright: str,
    ok: str = "green",
    err: str = "red",
    warn: str = "yellow",
) -> dict[str, str]:
    return {
        "fg": "default",
        "accent": accent,
        "accent.bright": accent_bright,
        "accent.shadow": _ACCENT_SHADOW_STYLE,
        "user_msg.bg": USER_MSG_BG,
        "code": accent,
        "muted": "bright_black",
        "border": "bright_black",
        "ok": ok,
        "warn": warn,
        "err": err,
        "diff.plus": ok,
        "diff.minus": err,
        "diff.meta": accent,
        "diff.context": "default",
        "approval.keys": "bright_black",
        "approval.default": "bold",
        "markdown.paragraph": "default",
        "markdown.text": "default",
        "markdown.em": "italic",
        "markdown.emph": "italic",
        "markdown.strong": "bold",
        "markdown.code": accent,
        "markdown.code_block": accent,
        "markdown.block_quote": "bright_black",
        "markdown.list": "default",
        "markdown.item": "default",
        "markdown.item.bullet": "bright_black",
        "markdown.item.number": "bright_black",
        "markdown.hr": "bright_black",
        "markdown.h1": f"bold {accent}",
        "markdown.h2": "bold",
        "markdown.h3": "bold bright_black",
        "markdown.h4": "bold bright_black",
        "markdown.h5": "italic",
        "markdown.h6": "bright_black",
        "markdown.link": accent,
        "markdown.link_url": f"{accent} underline",
    }


THEME: Final[Theme] = Theme(
    _base_theme_styles(accent=_ACCENT_STYLE, accent_bright=_ACCENT_BRIGHT_STYLE)
)

# Colour-blind: blue / orange instead of green / red for status meaning.
THEME_COLOUR_BLIND: Final[Theme] = Theme(
    _base_theme_styles(
        accent=_ACCENT_STYLE,
        accent_bright=_ACCENT_BRIGHT_STYLE,
        ok="bright_blue",
        err="dark_orange",
        warn="yellow",
    )
)

# Light terminal: keep accent; slightly stronger muted for contrast on light bg.
THEME_LIGHT: Final[Theme] = Theme(
    {
        **_base_theme_styles(accent=_ACCENT_STYLE, accent_bright=_ACCENT_BRIGHT_STYLE),
        "muted": "grey42",
        "border": "grey50",
        "user_msg.bg": "grey93",
        "markdown.code_block": _ACCENT_STYLE,
    }
)

PROMPT_STYLE: Final[Style] = Style.from_dict(
    {
        "": "ansibrightblack",
        "prompt": f"{ACCENT_HEX} bold",
        "bottom-toolbar": "noreverse ansibrightblack",
        "bottom-toolbar.text": "noreverse ansibrightblack",
        "bottom-toolbar.warn": "noreverse ansiyellow",
        "bottom-toolbar.err": "noreverse ansired",
        "completion-menu": "bg:default ansiwhite",
        "completion-menu.completion": "bg:default ansiwhite",
        "completion-menu.completion.current": f"{ACCENT_HEX} bold",
        "completion-menu.meta.completion": "bg:default ansibrightblack",
        "completion-menu.meta.completion.current": f"{ACCENT_HEX}",
        "ghost": f"{ACCENT_HEX} italic",
    }
)

# Inline choosers (`/model`, `/connectors`): the same meanings as the Rich theme.
PICKER_STYLE: Final[Style] = Style.from_dict(
    {
        "": "",
        "accent": ACCENT_HEX,
        "muted": "ansibrightblack",
        "ok": "ansigreen",
        "warn": "ansiyellow",
        "err": "ansired",
        "heading": "bold",
        "hint": "ansibrightblack",
        "selected": "bold",
    }
)

PLAIN_PROMPT_STYLE: Final[Style] = Style.from_dict(
    {
        "": "",
        "prompt": "",
        "bottom-toolbar": "noreverse",
        "bottom-toolbar.text": "noreverse",
        "bottom-toolbar.warn": "noreverse",
        "bottom-toolbar.err": "noreverse",
        "ghost": "italic",
    }
)

_DATED_SNAPSHOT_RE: Final[re.Pattern[str]] = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_SNAPSHOT_SUFFIXES: Final[tuple[str, ...]] = (
    "-chat-latest",
    "-codex",
    "-codex-max",
    "-codex-mini",
)


def rgb_style(r: int, g: int, b: int) -> str:
    """Return a Rich style string for an RGB colour."""
    return f"rgb({int(r)},{int(g)},{int(b)})"


def interpolate_rgb(
    a: tuple[int, int, int],
    b: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    """Linearly interpolate between RGB tuples; `t` clamped to ``0..1``."""
    clamped = max(0.0, min(1.0, float(t)))
    return (
        int(round(a[0] + (b[0] - a[0]) * clamped)),
        int(round(a[1] + (b[1] - a[1]) * clamped)),
        int(round(a[2] + (b[2] - a[2]) * clamped)),
    )


def resolve_theme_choice(name: str | None, *, plain: bool = False) -> ThemeChoice:
    """Map config / CLI flags to a concrete theme choice."""
    if plain or os.environ.get("NO_COLOR", "").strip():
        return "plain"
    cleaned = (name or "dark").strip().lower().replace("-", "_")
    if cleaned in {"default", "auto"}:
        return "auto"
    if cleaned in {"dark", "light", "colour_blind", "color_blind", "plain"}:
        if cleaned == "color_blind":
            return "colour_blind"
        return cleaned  # type: ignore[return-value]
    return "dark"


def rich_theme_for(choice: ThemeChoice) -> Theme:
    """Return the Rich ``Theme`` for a resolved choice (auto → dark for now)."""
    if choice == "plain":
        return THEME
    if choice == "light":
        return THEME_LIGHT
    if choice == "colour_blind":
        return THEME_COLOUR_BLIND
    # auto: prefer dark until OSC 11 background query lands with onboarding.
    return THEME


def context_bar(pct: int, *, plain: bool = False, width: int = 10) -> str:
    """Return a ``width``-char filled bar for remaining context percent."""
    bar_width = max(1, int(width))
    clamped = max(0, min(100, int(pct)))
    filled = int(round(clamped / 100.0 * bar_width))
    filled = max(0, min(bar_width, filled))
    if plain:
        return ("#" * filled) + ("-" * (bar_width - filled))
    return ("█" * filled) + ("░" * (bar_width - filled))


def reduced_motion_preferred() -> bool:
    """Return True when ``ALDWIN_REDUCED_MOTION=1`` asks for static chrome."""
    return os.environ.get("ALDWIN_REDUCED_MOTION", "").strip() == "1"


def sync_output_enabled(*, plain: bool) -> bool:
    """Return True when DEC 2026 sync brackets may wrap Live refreshes."""
    if plain:
        return False
    if os.environ.get("ALDWIN_NO_SYNC_OUTPUT", "").strip() == "1":
        return False
    if os.environ.get("TERM_PROGRAM", "").strip() == "Apple_Terminal":
        return False
    return True


def should_show_wordmark_splash(*, plain: bool, width: int) -> bool:
    """Return True when the ALDWIN wordmark splash may play this launch."""
    return not plain and not reduced_motion_preferred() and width >= 80


def set_terminal_title(
    file: IO[str] | None, title: str, *, plain: bool = False
) -> None:
    """Set the terminal window/tab title via OSC 0 (no-op when plain)."""
    if plain or file is None:
        return
    clean = title.replace("\x1b", "").replace("\x07", "")[:80]
    try:
        file.write(f"\x1b]0;{clean}\x07")
        file.flush()
    except OSError:
        pass


def clear_terminal_title(file: IO[str] | None, *, plain: bool = False) -> None:
    """Restore a neutral terminal title after a working turn."""
    set_terminal_title(file, "Aldwin", plain=plain)


def model_context_window(model: str) -> int:
    """Return the nominal context window in tokens for `model`."""
    lowered = model.strip().lower().split("/")[-1]
    if lowered.startswith(("gpt-4.1", "gpt-5")):
        return 400_000
    if lowered.startswith(("o3", "o4")):
        return 200_000
    if lowered.startswith("claude"):
        return 200_000
    return 128_000


def context_left_percent(model: str, used_tokens: int) -> int:
    """Return remaining context percentage (`0..100`) for `model`."""
    window = max(1, model_context_window(model))
    clamped_used = max(0, int(used_tokens))
    remaining = max(0.0, float(window - clamped_used)) / float(window) * 100.0
    return max(0, min(100, round(remaining)))


def context_meter_level(pct: int) -> Literal["ok", "warn", "err"]:
    """Return footer meter severity for remaining context percent."""
    if pct <= 10:
        return "err"
    if pct <= 20:
        return "warn"
    return "ok"


def is_canonical_model(model_id: str) -> bool:
    """Return `True` if `model_id` is a canonical model alias rather than a snapshot."""
    cleaned = model_id.strip().lower()
    if not cleaned:
        return False
    if _DATED_SNAPSHOT_RE.search(cleaned):
        return False
    return not any(cleaned.endswith(suffix) for suffix in _SNAPSHOT_SUFFIXES)


def filter_canonical_models(
    models: Sequence[str],
    current_model: str,
) -> tuple[list[str], int]:
    """Filter `models` to canonical aliases with `current_model` first.

    Returns `(filtered_models, hidden_snapshot_count)`. Falls back to the full
    list if filtering would leave zero models.
    """
    unique: list[str] = []
    seen: set[str] = set()
    for name in models:
        cleaned = name.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)

    current_clean = current_model.strip()
    canonical = [m for m in unique if m == current_clean or is_canonical_model(m)]
    if not canonical:
        canonical = list(unique)

    if current_clean and current_clean in canonical:
        canonical = [
            current_clean,
            *[m for m in canonical if m != current_clean],
        ]
    elif current_clean:
        canonical = [current_clean, *canonical]

    hidden_count = max(0, len(unique) - len(canonical))
    return canonical, hidden_count


def create_console(
    *,
    plain: bool = False,
    width: int | None = None,
    record: bool = False,
    file: IO[str] | None = None,
    force_terminal: bool | None = None,
    theme: ThemeChoice | str | None = None,
) -> Console:
    """Create the shared Rich `Console` configured with Aldwin's theme."""
    name = theme if isinstance(theme, str) else None
    choice = resolve_theme_choice(name, plain=plain)
    return Console(
        theme=rich_theme_for(choice),
        no_color=plain or choice == "plain",
        highlight=False,
        width=width,
        record=record,
        file=file,
        force_terminal=False if plain and force_terminal is None else force_terminal,
    )


def format_tokens(count: int) -> str:
    """Format a token count concisely (`6200` -> `'6.2k'`, `450` -> `'450'`)."""
    clamped = max(0, int(count))
    if clamped < 1000:
        return str(clamped)
    return f"{clamped / 1000:.1f}k"


def format_duration_ms(duration_ms: int) -> str:
    """Format milliseconds as seconds with one decimal place (`1200` -> `'1.2s'`)."""
    seconds = max(0, int(duration_ms)) / 1000.0
    return f"{seconds:.1f}s"


def format_elapsed_s(seconds: float) -> str:
    """Format elapsed turn duration in whole seconds (`24.3` -> `'24s'`)."""
    whole = max(0, int(seconds))
    return f"{whole}s"


def format_relative_time(
    iso_ts: str | None,
    *,
    now: datetime | None = None,
) -> str:
    """Format an ISO-8601 timestamp for the history divider."""
    if not iso_ts:
        return "earlier"
    try:
        parsed = datetime.fromisoformat(iso_ts)
    except ValueError:
        return "earlier"

    if now is None:
        local_now = datetime.now().astimezone()
    elif now.tzinfo is None:
        local_now = now.astimezone()
    else:
        local_now = now
    local_ts = parsed.astimezone(local_now.tzinfo)
    time_part = local_ts.strftime("%H:%M")

    if local_ts.date() == local_now.date():
        return f"today {time_part}"
    if local_ts.date() == (local_now.date() - timedelta(days=1)):
        return f"yesterday {time_part}"
    return f"{local_ts.strftime('%Y-%m-%d')} {time_part}"
