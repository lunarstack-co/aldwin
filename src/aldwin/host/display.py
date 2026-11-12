"""Host validation, display sanitising/redaction, and context-usage builder."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final

from pydantic import ValidationError

from aldwin.core.policy import sanitize_display_text
from aldwin.protocol.display import (
    ContextSegment,
    ContextUsage,
    Display,
    validate_display,
)

logger = logging.getLogger("aldwin.host.display")

CHARS_PER_TOKEN: Final[float] = 3.5

_SEGMENT_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("instructions", "Instructions"),
    ("skills", "Skills"),
    ("persona", "Persona"),
    ("memory", "Memory"),
    ("environment", "Environment"),
    ("connectors", "Connected apps"),
    ("conversation", "Conversation"),
)


def _sanitize_payload(value: Any) -> Any:
    """Recursively strip ANSI/control/bidi characters and redact secrets."""
    if isinstance(value, str):
        return sanitize_display_text(value)
    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            (sanitize_display_text(k) if isinstance(k, str) else k): _sanitize_payload(
                v
            )
            for k, v in value.items()
        }
    return value


def to_display(raw: Any, *, tool: str = "") -> Display | None:
    """Sanitise, redact secrets, and validate `raw` into a protocol `Display`.

    Invalid payloads are dropped with a single warning so a malformed display
    never blocks a turn.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.warning(
            "Dropping invalid display for tool %s: expected dict, got %s",
            tool or "unknown",
            type(raw).__name__,
        )
        return None

    cleaned = _sanitize_payload(raw)
    try:
        return validate_display(cleaned)
    except ValidationError as exc:
        logger.warning(
            "Dropping invalid display for tool %s: %s",
            tool or "unknown",
            exc,
        )
        return None


def _apportion_tokens(raw_estimates: list[float], target_total: int) -> list[int]:
    """Scale non-negative `raw_estimates` so integer tokens sum to `target_total`."""
    if target_total <= 0 or not raw_estimates:
        return [0 for _ in raw_estimates]
    total_raw = sum(raw_estimates)
    if total_raw <= 0:
        return [0 for _ in raw_estimates]

    exact = [max(0.0, val) * target_total / total_raw for val in raw_estimates]
    floors = [int(val) for val in exact]
    remainder = target_total - sum(floors)
    order = sorted(
        range(len(exact)),
        key=lambda idx: (exact[idx] - floors[idx], exact[idx]),
        reverse=True,
    )
    for idx in order[: max(0, remainder)]:
        floors[idx] += 1
    return floors


def context_usage(
    chars: Mapping[str, int],
    *,
    used: int,
    limit: int,
) -> ContextUsage:
    """Build a `ContextUsage` breakdown scaled to `used` input tokens."""
    safe_used = max(0, int(used))
    safe_limit = max(1, int(limit))

    raw_estimates = [
        max(0, int(chars.get(key, 0))) / CHARS_PER_TOKEN for key, _ in _SEGMENT_LABELS
    ]
    rounded = [int(round(val)) for val in raw_estimates]
    sum_rounded = sum(rounded)

    if sum_rounded <= safe_used:
        segment_tokens = rounded
        tools_tokens = safe_used - sum_rounded
    else:
        segment_tokens = _apportion_tokens(raw_estimates, safe_used)
        tools_tokens = 0

    segments = [
        ContextSegment(key=key, label=label, tokens=tokens)
        for (key, label), tokens in zip(_SEGMENT_LABELS, segment_tokens, strict=True)
    ]
    segments.append(
        ContextSegment(key="tools", label="Tool definitions", tokens=tools_tokens)
    )
    return ContextUsage(
        limit=safe_limit,
        used=safe_used,
        segments=segments,
    )
