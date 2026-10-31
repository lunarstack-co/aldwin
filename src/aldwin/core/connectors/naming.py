"""Model-facing names for connector tools: `mcp__<connector>__<tool>`."""

from __future__ import annotations

import hashlib
import re

MODEL_NAME_PREFIX = "mcp__"
MAX_MODEL_NAME = 64
_CLAMP_AT = 55
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def model_tool_name(connector: str, tool: str) -> str:
    """Return the stable name the model sees for `tool` on `connector`.

    Characters outside `[A-Za-z0-9_-]` become `_`. Names over 64 characters (the
    OpenAI limit) are cut to 55 and end in `_` plus 8 hex characters of the
    sha256 of the full name, so long names stay unique and stable across runs.
    """
    full = _UNSAFE.sub("_", f"{MODEL_NAME_PREFIX}{connector}__{tool}")
    if len(full) <= MAX_MODEL_NAME:
        return full
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()[:8]
    return f"{full[:_CLAMP_AT]}_{digest}"
