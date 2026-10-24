"""E2E fixtures: skip without `OPENAI_API_KEY`; shared canary constant."""

from __future__ import annotations

import os

import pytest
from canary import CANARY

pytestmark = pytest.mark.e2e


def pytest_configure(config: pytest.Config) -> None:
    """Expose canary on the config object for e2e modules."""
    config.ALDWIN_CANARY = CANARY  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def openai_api_key() -> str:
    """Require `OPENAI_API_KEY` for e2e; skip cleanly when unset."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        pytest.skip("OPENAI_API_KEY is unset; skipping e2e")
    return key
