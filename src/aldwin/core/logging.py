"""Rotating file logging configuration and secret redaction filter for Aldwin."""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from aldwin.core.home import DIR_MODE, FILE_MODE, ensure_permissions, home_dir
from aldwin.core.settings import mask_secret

LOGGER_NAME: Final[str] = "aldwin"
MCP_LOGGER_NAME: Final[str] = "mcp"
LOG_REL_PATH: Final[str] = "logs/aldwin.log"
MAX_LOG_BYTES: Final[int] = 1_000_000
BACKUP_COUNT: Final[int] = 3
LOG_FORMAT: Final[str] = "%(asctime)s %(levelname)s %(name)s %(message)s"

_SECRET_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:sk-(?:proj-|ant-)?|tvly-(?:dev-|prod-)?)[A-Za-z0-9_-]{8,}\b"
)

_OAUTH_FIELDS: Final[str] = (
    "code|state|code_verifier|access_token|refresh_token|id_token|client_secret"
)
_OAUTH_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(rf"(?i)\b({_OAUTH_FIELDS})=[^&\s\"']+"), r"\1=***"),
    (re.compile(rf'(?i)"({_OAUTH_FIELDS})"\s*:\s*"[^"]*"'), r'"\1": "***"'),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
)


def _redact_text(text: str) -> str:
    """Mask API keys, and OAuth codes and tokens, in ``text``."""
    text = _SECRET_TOKEN_PATTERN.sub(lambda m: mask_secret(m.group(0)), text)
    for pattern, replacement in _OAUTH_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretRedactingFilter(logging.Filter):
    """Logging filter that masks secrets before records reach disk."""

    def filter(self, record: logging.LogRecord) -> bool:
        formatted = record.getMessage()
        redacted = _redact_text(formatted)
        record.msg = redacted
        record.args = ()
        if record.exc_info and not record.exc_text:
            # Tracebacks can quote URLs and response bodies; redact them too.
            record.exc_text = _redact_text(
                logging.Formatter().formatException(record.exc_info)
            )
        return True


class QuietOAuthFlowFilter(logging.Filter):
    """Drops the SDK's traceback for a sign-in Aldwin already explained."""

    def filter(self, record: logging.LogRecord) -> bool:
        from aldwin.core.connectors.errors import SignInError

        if not record.name.startswith(MCP_LOGGER_NAME):
            return True
        error = record.exc_info[1] if record.exc_info else None
        return not isinstance(error, SignInError)


def setup_logging(home: Path | None = None, *, debug: bool = False) -> Path:
    """Configure rotating file logging in ``~/.aldwin/logs/aldwin.log``.

    Creates ``logs/`` (mode ``0700``) and ``aldwin.log`` (mode ``0600``), rotates
    at 1 MB keeping 3 backups, attaches ``SecretRedactingFilter``, and disables
    propagation so log lines never pollute terminal output.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    log_path = root / LOG_REL_PATH
    log_dir = log_path.parent
    log_dir.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    ensure_permissions(log_dir, DIR_MODE)

    aldwin_logger = logging.getLogger(LOGGER_NAME)
    aldwin_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    aldwin_logger.propagate = False

    for existing_handler in list(aldwin_logger.handlers):
        aldwin_logger.removeHandler(existing_handler)
        existing_handler.close()

    handler = RotatingFileHandler(
        log_path,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    os.chmod(log_path, FILE_MODE)

    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(QuietOAuthFlowFilter())
    handler.addFilter(SecretRedactingFilter())
    aldwin_logger.addHandler(handler)

    # Unconfigured, the SDK's warnings would reach the terminal through the root logger.
    mcp_logger = logging.getLogger(MCP_LOGGER_NAME)
    mcp_logger.setLevel(logging.WARNING)
    mcp_logger.propagate = False
    for existing_handler in list(mcp_logger.handlers):
        mcp_logger.removeHandler(existing_handler)
    mcp_logger.addHandler(handler)

    return log_path
