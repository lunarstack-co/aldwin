"""Unit tests for fixture recorder credential sanitization."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from record_provider_fixture import sanitize_exchange  # noqa: E402


def test_sanitize_exchange_redacts_auth_headers_and_body() -> None:
    """Authorization headers and embedded secrets are scrubbed before saving."""
    exchange = SimpleNamespace(
        request={
            "method": "POST",
            "url": "https://api.anthropic.com/v1/messages",
            "headers": {
                "Authorization": "Bearer sk-canary-secret-token",
                "X-Api-Key": "sk-ant-canary-key",
                "User-Agent": "python-httpx/0.28.1",
            },
            "body": {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Secret key is sk-ant-api03-canary7f3a99118822xx inside."
                        ),
                    }
                ]
            },
        }
    )

    sanitize_exchange(exchange)

    headers = exchange.request["headers"]
    assert headers["Authorization"] == "‹redacted›"
    assert headers["X-Api-Key"] == "‹redacted›"
    assert headers["User-Agent"] == "python-httpx/0.28.1"

    body_content = exchange.request["body"]["messages"][0]["content"]
    assert "sk-ant-api03-canary7f3a99118822xx" not in body_content
    assert "‹redacted›" in body_content
