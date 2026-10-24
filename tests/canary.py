"""Shared canary constant and scan helper for the security suite."""

from __future__ import annotations

from pathlib import Path

CANARY = "ALDWIN_CANARY_7f3a"


def scan_for_canary(paths: list[Path], texts: list[str]) -> list[str]:
    """Return human-readable hit descriptions when `CANARY` appears anywhere."""
    hits: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file():
                    hits.extend(_scan_file(child))
            continue
        hits.extend(_scan_file(path))
    for index, text in enumerate(texts):
        if CANARY in text:
            hits.append(f"text[{index}]")
    return hits


def _scan_file(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        if CANARY.encode("ascii") in data:
            return [f"{path} (binary)"]
        return []
    if CANARY in text:
        return [str(path)]
    return []
