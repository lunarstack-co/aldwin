"""Token shapes that should never be pasted into chat.

Stdlib only, so doors and the host share one definition. This is a
best-effort check on shape, not proof that a match is a live credential.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

REDACTED: Final[str] = "‹redacted›"

SECRET_SHAPES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("github", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
)


@dataclass(frozen=True)
class SecretMatch:
    kind: str
    start: int
    end: int


def find_pasted_secrets(text: str) -> list[SecretMatch]:
    """Every token-shaped span in `text`, in order, with overlaps merged."""
    found = sorted(
        (
            SecretMatch(kind, match.start(), match.end())
            for kind, pattern in SECRET_SHAPES
            for match in pattern.finditer(text)
        ),
        key=lambda found: (found.start, -found.end),
    )
    merged: list[SecretMatch] = []
    for match in found:
        # A shape inside a longer one (a `-sk-` run inside a JWT) is part of it.
        if merged and match.start < merged[-1].end:
            last = merged[-1]
            if match.end > last.end:
                merged[-1] = SecretMatch(last.kind, last.start, match.end)
            continue
        merged.append(match)
    return merged


def redact_pasted_secrets(text: str) -> str:
    """`text` with every span `find_pasted_secrets` reports replaced."""
    for match in reversed(find_pasted_secrets(text)):
        text = text[: match.start] + REDACTED + text[match.end :]
    return text
