"""Recently used skill names for the `@` picker (`skills_recent.json`)."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from aldwin.core.home import FILE_MODE, atomic_write, home_dir

logger = logging.getLogger("aldwin.core.skills_recent")

STATE_SUBDIR: Final[str] = "state"
RECENT_FILENAME: Final[str] = "skills_recent.json"
MAX_RECENT: Final[int] = 50


def skills_recent_path(home: Path | None = None) -> Path:
    """Return `~/.aldwin/state/skills_recent.json` under `home`."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    return root / STATE_SUBDIR / RECENT_FILENAME


def load_skills_recent(home: Path | None = None) -> dict[str, str]:
    """Load `{name: ISO timestamp}` map. Missing or corrupt files yield `{}`."""
    path = skills_recent_path(home)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring corrupt skills_recent.json at %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and key and value:
            out[key] = value
    return out


def record_skills_used(
    home: Path | None = None,
    names: Sequence[str] = (),
    *,
    now: datetime | None = None,
) -> None:
    """Record resolved picks, keeping the 50 newest names (atomic, mode 0600)."""
    if not names:
        return
    root = (home if home is not None else home_dir()).expanduser().resolve()
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    current = load_skills_recent(root)
    for name in names:
        cleaned = name.strip()
        if cleaned:
            current[cleaned] = stamp
    ordered = sorted(current.items(), key=lambda kv: kv[1], reverse=True)
    trimmed = dict(ordered[:MAX_RECENT])
    path = skills_recent_path(root)
    atomic_write(
        path,
        json.dumps(trimmed, indent=2, sort_keys=True) + "\n",
        mode=FILE_MODE,
    )


__all__ = [
    "MAX_RECENT",
    "RECENT_FILENAME",
    "STATE_SUBDIR",
    "load_skills_recent",
    "record_skills_used",
    "skills_recent_path",
]
