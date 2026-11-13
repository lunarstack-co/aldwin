"""Persona file reading, cap truncation, and atomic updates for Aldwin."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Final

from aldwin.core.home import FILE_MODE, atomic_write, format_home_path, home_dir

logger = logging.getLogger("aldwin.core.persona")

PERSONA_FILENAME: Final[str] = "persona.md"
DEFAULT_PERSONA_CAP: Final[int] = 2000

_WARNED_PERSONA_CAPS: set[tuple[str, int, int, int]] = set()


@dataclass(frozen=True, slots=True)
class PersonaSummary:
    """Summary of the active `persona.md` file used by `/persona`."""

    path: Path
    display_path: str
    text: str
    size_chars: int
    cap_chars: int
    truncated: bool


def read_default_persona() -> str:
    """Read the package-shipped default `persona.md` asset."""
    return (
        files("aldwin.core.assets")
        .joinpath(PERSONA_FILENAME)
        .read_text(encoding="utf-8")
        .strip()
    )


def _read_raw_persona(root: Path) -> tuple[Path, str, int]:
    """Return `(persona_path, stripped_text, mtime_ns)` with shipped fallback."""
    persona_path = root / PERSONA_FILENAME
    mtime_ns = 0
    try:
        stat = persona_path.stat()
        mtime_ns = stat.st_mtime_ns
        raw = persona_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        raw = ""

    if not raw:
        raw = read_default_persona()

    return persona_path, raw, mtime_ns


def read_persona(
    home: Path | None = None,
    cap: int = DEFAULT_PERSONA_CAP,
) -> str:
    """Read `persona.md` from `home` (or shipped default), capped at `cap`.

    Truncates in the prompt only (never on disk) and logs a warning once per
    file modification when the file exceeds `cap` characters.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    persona_path, raw, mtime_ns = _read_raw_persona(root)

    if len(raw) > cap:
        warn_key = (str(persona_path), mtime_ns, len(raw), cap)
        if warn_key not in _WARNED_PERSONA_CAPS:
            if len(_WARNED_PERSONA_CAPS) >= 64:
                _WARNED_PERSONA_CAPS.clear()
            _WARNED_PERSONA_CAPS.add(warn_key)
            msg = "Persona file %s is %d characters (cap %d); truncating in prompt."
            logger.warning(msg, persona_path, len(raw), cap)
            context_logger = logging.getLogger("aldwin.core.context")
            if not logging.getLogger("aldwin").propagate and context_logger.handlers:
                context_logger.warning(msg, persona_path, len(raw), cap)
        return raw[:cap]

    return raw


def persona_text(home: Path | None = None) -> str:
    """Return the current `persona.md` text (or default) for `/persona`."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    _path, raw, _mtime = _read_raw_persona(root)
    return raw


def persona_summary(
    home: Path | None = None,
    cap: int = DEFAULT_PERSONA_CAP,
) -> PersonaSummary:
    """Return path, text, and cap metadata for `/persona`."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    persona_path, raw, _mtime = _read_raw_persona(root)
    return PersonaSummary(
        path=persona_path,
        display_path=format_home_path(persona_path),
        text=raw,
        size_chars=len(raw),
        cap_chars=cap,
        truncated=len(raw) > cap,
    )


def write_persona(
    home: Path | None = None,
    new_text: str = "",
    *,
    cap: int = DEFAULT_PERSONA_CAP,
) -> Path:
    """Validate `new_text` (`1..cap` chars) and write `persona.md`."""
    if not isinstance(new_text, str) or "\x00" in new_text:
        raise ValueError("Invalid persona: text must be a valid string.")

    cleaned = new_text.strip()
    if not cleaned or len(cleaned) > cap:
        raise ValueError(f"Invalid persona: must be between 1 and {cap:,} characters.")

    root = (home if home is not None else home_dir()).expanduser().resolve()
    persona_path = root / PERSONA_FILENAME
    atomic_write(persona_path, f"{cleaned}\n", mode=FILE_MODE)
    return persona_path
