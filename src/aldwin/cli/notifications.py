"""Terminal desktop notifications with a bell fallback."""

from __future__ import annotations

import os
import re
from typing import IO


def notify_terminal(
    file: IO[str],
    message: str | None = None,
    *,
    plain: bool = False,
    is_terminal: bool = False,
) -> None:
    """Notify through the terminal, or ring its bell for unknown terminals."""
    if plain or "NO_COLOR" in os.environ or not is_terminal:
        return

    sequence = "\a"
    if message is not None:
        # Keep payloads on one line and prevent OSC terminators or extra fields.
        clean = re.sub(r"[\x00-\x1f\x7f-\x9f;]", "", message)[:160]
        program = os.environ.get("TERM_PROGRAM", "").strip().casefold()
        if program in {"iterm.app", "ghostty", "wezterm", "kitty"}:
            sequence = f"\x1b]9;{clean}\a"
        elif program in {"vscode", "konsole"}:
            sequence = f"\x1b]777;notify;Aldwin;{clean}\a"
        elif "KITTY_WINDOW_ID" in os.environ:
            sequence = f"\x1b]9;{clean}\a"
        elif "KONSOLE_VERSION" in os.environ:
            sequence = f"\x1b]777;notify;Aldwin;{clean}\a"
    try:
        file.write(sequence)
        file.flush()
    except OSError:
        pass
