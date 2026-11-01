"""Unit tests for aldwin.core.logging."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from aldwin.core.connectors.errors import SignInTimedOut
from aldwin.core.logging import (
    BACKUP_COUNT,
    MAX_LOG_BYTES,
    setup_logging,
)


def test_setup_logging_rotates_and_respects_debug_flag(
    aldwin_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """TC-25: RotatingFileHandler writes 0600 log, honours debug, stays off stdout."""
    log_path = setup_logging(aldwin_home, debug=False)
    assert log_path == aldwin_home / "logs" / "aldwin.log"
    assert log_path.is_file()
    assert (log_path.stat().st_mode & 0o777) == 0o600

    root_logger = logging.getLogger("aldwin")
    rotating_handlers = [
        h for h in root_logger.handlers if isinstance(h, RotatingFileHandler)
    ]
    assert len(rotating_handlers) == 1
    assert rotating_handlers[0].maxBytes == MAX_LOG_BYTES == 1_000_000
    assert rotating_handlers[0].backupCount == BACKUP_COUNT == 3

    child_logger = logging.getLogger("aldwin.core.test")
    child_logger.info("startup_check_ok")
    child_logger.debug("hidden_when_not_debug")

    contents = log_path.read_text(encoding="utf-8")
    assert "INFO aldwin.core.test startup_check_ok" in contents
    assert "hidden_when_not_debug" not in contents

    setup_logging(aldwin_home, debug=True)
    child_logger.debug("visible_when_debug")

    updated_contents = log_path.read_text(encoding="utf-8")
    assert "DEBUG aldwin.core.test visible_when_debug" in updated_contents

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_logging_redacts_accidental_secret_tokens(aldwin_home: Path) -> None:
    """TC-26: SecretRedactingFilter scrubs API keys before writing to aldwin.log."""
    log_path = setup_logging(aldwin_home, debug=True)
    logger = logging.getLogger("aldwin.core.test")

    logger.error(
        "Auth failed for %s and %s",
        "sk-proj-1234567890abcdef",
        "tvly-dev-9876543210fedcba",
    )

    contents = log_path.read_text(encoding="utf-8")
    assert "sk-…cdef" in contents
    assert "tvly-…dcba" in contents
    assert "1234567890" not in contents
    assert "9876543210" not in contents


def test_logging_redacts_oauth_codes_and_tokens(aldwin_home: Path) -> None:
    log_path = setup_logging(aldwin_home)
    logger = logging.getLogger("aldwin.core.test")
    logger.warning(
        "callback http://127.0.0.1:47823/callback?code=c-4417&state=s-0921 "
        'body {"access_token": "at-5521", "refresh_token": "rt-7730"} '
        "header Authorization: Bearer at-5521"
    )
    try:
        raise ValueError("token endpoint said code_verifier=v-3319")
    except ValueError:
        logger.exception("exchange failed")

    contents = log_path.read_text(encoding="utf-8")
    for secret in ("c-4417", "s-0921", "at-5521", "rt-7730", "v-3319"):
        assert secret not in contents
    assert "code=***" in contents and "Bearer ***" in contents


def test_mcp_warnings_go_to_the_log_file_not_the_terminal(
    aldwin_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = setup_logging(aldwin_home)
    sdk = logging.getLogger("mcp.client.auth.oauth2")
    sdk.info("discovery chatter")
    sdk.warning("Token refresh failed: 400")
    try:
        raise SignInTimedOut()
    except SignInTimedOut:
        sdk.exception("OAuth flow error")

    contents = log_path.read_text(encoding="utf-8")
    assert "Token refresh failed: 400" in contents
    assert "discovery chatter" not in contents
    assert "OAuth flow error" not in contents
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out == ""
