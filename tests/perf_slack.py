"""Scale perf budgets on shared CI runners without relaxing laptop targets."""

from __future__ import annotations

import os


def budget(seconds: float) -> float:
    """Return `seconds`, or 3× that limit when running under GitHub Actions."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return seconds * 3.0
    return seconds
