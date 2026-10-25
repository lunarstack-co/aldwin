"""Rotating everyday-launch tips for the welcome card."""

from __future__ import annotations

from pathlib import Path
from typing import Final

TIPS: Final[tuple[str, ...]] = (
    "Try @ to pick a skill · /skills lists them",
    "Try: find every TODO in this folder",
    "Try: summarise report.pdf",
    "Switch models anytime with /model",
    "Press Esc to stop an answer in progress",
    "Alt+Enter (or Shift+Enter) inserts a newline",
    "Use /verbose to see tool output previews",
    'Try: "what can you do?" for a quick tour',
    "Type /help for slash commands and shortcuts",
    "Press ? for the shortcuts panel",
    "Use /todo to review the task checklist",
    "Use /workspace to set where file tools run",
    "Use /memory to browse notes and profile",
    "Use /new when context gets tight",
)

_STATE_NAME: Final[str] = "last_tip.txt"


def _state_path(home: Path) -> Path:
    return home.expanduser().resolve() / "state" / _STATE_NAME


def _read_last(home: Path) -> str | None:
    path = _state_path(home)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _write_last(home: Path, tip: str) -> None:
    path = _state_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tip + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        return


def next_tip(home: Path, *, plain: bool = False) -> str | None:
    """Return the next tip for an everyday launch, never repeating the previous.

    Plain mode shows no tip. Persists the chosen tip under
    ``~/.aldwin/state/last_tip.txt`` so consecutive launches rotate.
    """
    if plain:
        return None
    if not TIPS:
        return None

    previous = _read_last(home)
    if previous is None or previous not in TIPS:
        chosen = TIPS[0]
    else:
        idx = TIPS.index(previous)
        chosen = TIPS[(idx + 1) % len(TIPS)]

    _write_last(home, chosen)
    return chosen
