"""Connector test fixtures shared by the files in this folder."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from aldwin.core.connectors import stdio


@pytest.fixture(autouse=True)
def live_groups_cleanup() -> Iterator[None]:
    """A failing process test must never leave a server running."""
    try:
        yield
    finally:
        leaked = set(stdio._LIVE_PGIDS)
        stdio.kill_live_groups()
        assert not leaked, f"process groups left running: {sorted(leaked)}"
