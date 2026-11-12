"""Connected apps picked for one message: which count, and the note to the model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Literal

MAX_PICKED_APPS: Final[int] = 3

PickDrop = Literal["unknown", "limit"]


def resolve_picked_apps(
    names: Sequence[str],
    connected: Mapping[str, str],
    *,
    limit: int = MAX_PICKED_APPS,
) -> tuple[list[tuple[str, str]], list[tuple[str, PickDrop]]]:
    """Validate picked connector names against `connected` (name to title).

    Dedupes, drops names that are not connected right now, and caps at `limit`.
    Returns `(picked, dropped)`; each pick is `(name, title)`.
    """
    picked: list[tuple[str, str]] = []
    dropped: list[tuple[str, PickDrop]] = []
    seen: set[str] = set()
    for name in names:
        cleaned = name.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        if cleaned not in connected:
            dropped.append((cleaned, "unknown"))
        elif len(picked) >= limit:
            dropped.append((cleaned, "limit"))
        else:
            picked.append((cleaned, connected[cleaned]))
    return picked, dropped


def _plain(text: str) -> str:
    # Titles come from config and servers; they must not fake the block's tags.
    return text.replace("<", "&lt;").replace(">", "&gt;")


def build_picked_apps(task_text: str, apps: Sequence[tuple[str, str]]) -> str:
    """Put the picked apps ahead of the task; unchanged when there are none."""
    if not apps:
        return task_text
    lines = "".join(f"- {_plain(title)} (connector: {name})\n" for name, title in apps)
    return (
        "<picked_apps>\n"
        "The user wants you to use these connected apps for the request below. "
        "Load the tools you need with find_connector_tools, giving the connector "
        "name.\n"
        f"{lines}"
        "</picked_apps>\n\n"
        f"{task_text}"
    )
