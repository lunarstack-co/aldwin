"""Structured approval audit log at `~/.aldwin/logs/approvals.jsonl`."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final, Literal

from aldwin.core.home import DIR_MODE, FILE_MODE, ensure_permissions, home_dir

logger = logging.getLogger("aldwin.core.approval_log")

APPROVAL_LOG_REL_PATH: Final[str] = "logs/approvals.jsonl"
MAX_APPROVAL_LOG_BYTES: Final[int] = 10 * 1024 * 1024  # 10 MB
APPROVAL_LOG_BACKUP_COUNT: Final[int] = 3
MAX_TARGET_CHARS: Final[int] = 512
REDACTED_PLACEHOLDER: Final[str] = "‹redacted›"

LogTier = Literal["auto", "diff", "ask"]
LogDecision = Literal["auto", "asked", "blocked"]
LogAnswer = Literal["yes", "yes_session", "no", "none"]
LogSource = Literal["rule", "grant", "user", "no_approver", "error", "hint"]

# Shared secret patterns for panel redaction, audit logging, and memory scrubbing:
# - sk-[A-Za-z0-9_-]{20,} (OpenAI / Anthropic keys)
# - tvly-[A-Za-z0-9_-]{12,} (Tavily keys)
# - AKIA[0-9A-Z]{16} (AWS access key IDs)
# - gh[pousr]_[A-Za-z0-9]{36,} (GitHub tokens)
# - xox[abprs]-[A-Za-z0-9-]{10,} (Slack tokens)
# - -----BEGIN ... PRIVATE KEY----- blocks or headers
SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"-----BEGIN [A-Z0-9 _-]*PRIVATE KEY-----"
        r"[\s\S]*?"
        r"-----END [A-Z0-9 _-]*PRIVATE KEY-----"
    ),
    re.compile(r"-----BEGIN [A-Z0-9 _-]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\btvly-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bALDWIN_CANARY_[A-Za-z0-9_-]+\b"),
)
# Back-compat alias used by older call sites.
_SECRET_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = SECRET_PATTERNS

_SECRET_ASSIGN_RE: Final[re.Pattern[str]] = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Za-z0-9_]*)"
    r"\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s;&|\"']+)",
    re.IGNORECASE,
)

# URL / form query params: `token=…`, `password=…`, `api_key=…`, etc.
# Bare `key=` is omitted so sorting params like `?key=name` stay readable.
_SECRET_QUERY_RE: Final[re.Pattern[str]] = re.compile(
    r"([?&#]?)(token|secret|password|passwd|credential|api_key|access_token)"
    r"=([^\s&#]*)",
    re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    """Replace secret-shaped values and `NAME=value` assignments with `‹redacted›`."""
    if not text:
        return ""
    redacted = _SECRET_ASSIGN_RE.sub(rf"\1={REDACTED_PLACEHOLDER}", text)
    redacted = _SECRET_QUERY_RE.sub(
        rf"\1\2={REDACTED_PLACEHOLDER}",
        redacted,
    )
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(REDACTED_PLACEHOLDER, redacted)
    return redacted


def _cap_target(target: str, limit: int = MAX_TARGET_CHARS) -> str:
    """Redact secrets from `target` and truncate to at most `limit` characters."""
    cleaned = redact_secrets(target)
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1]}…"


class ApprovalLogger:
    """Writes one JSON line per policy decision to `~/.aldwin/logs/approvals.jsonl`.

    Rotates at 10 MB with 3 backups (`0600` file in `0700` folder). Never blocks a
    tool call if disk writes fail: emits at most one warning per session.
    """

    def __init__(
        self,
        home: Path | None = None,
        *,
        session_id_getter: Callable[[], str] | None = None,
        max_bytes: int = MAX_APPROVAL_LOG_BYTES,
        backup_count: int = APPROVAL_LOG_BACKUP_COUNT,
    ) -> None:
        self._home = home
        self._session_id_getter = session_id_getter
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._handler: RotatingFileHandler | None = None
        self._warned_failure: bool = False

    @property
    def log_path(self) -> Path:
        """Return the resolved path to `approvals.jsonl`."""
        root = (
            (self._home if self._home is not None else home_dir())
            .expanduser()
            .resolve()
        )
        return root / APPROVAL_LOG_REL_PATH

    def _ensure_handler(self) -> RotatingFileHandler:
        if self._handler is not None:
            return self._handler

        path = self.log_path
        log_dir = path.parent
        log_dir.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        ensure_permissions(log_dir, DIR_MODE)

        handler = RotatingFileHandler(
            path,
            maxBytes=self._max_bytes,
            backupCount=self._backup_count,
            encoding="utf-8",
        )
        os.chmod(path, FILE_MODE)
        self._handler = handler
        return handler

    def log_decision(
        self,
        *,
        call_id: str,
        tool: str,
        target: str,
        digest: str | None,
        tier: LogTier,
        decision: LogDecision,
        answer: LogAnswer,
        source: LogSource,
        reason: str | None = None,
        session_id: str | None = None,
        connector: str | None = None,
    ) -> bool:
        """Append one redacted JSON record to `approvals.jsonl`.

        Returns `True` if the line was written, or `False` if an I/O error
        occurred (logging at most one warning for this logger instance).
        """
        active_session = session_id
        if not active_session and self._session_id_getter is not None:
            try:
                active_session = self._session_id_getter()
            except Exception:
                active_session = "unknown"
        if not active_session:
            active_session = "default"

        record_obj = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "session": active_session,
            "call_id": call_id,
            "tool": tool,
            "target": _cap_target(target),
            "digest": digest,
            "tier": tier,
            "decision": decision,
            "answer": answer,
            "source": source,
            "reason": redact_secrets(reason) if reason else None,
            "connector": connector,
        }
        line = json.dumps(record_obj, ensure_ascii=False)

        try:
            handler = self._ensure_handler()
            stream = handler.stream
            assert stream is not None
            msg_bytes = len(line.encode("utf-8")) + 1
            if stream.tell() + msg_bytes >= self._max_bytes:
                handler.doRollover()
                os.chmod(self.log_path, FILE_MODE)
                stream = handler.stream
                assert stream is not None
            stream.write(line + "\n")
            stream.flush()
            return True
        except Exception as exc:
            if not self._warned_failure:
                self._warned_failure = True
                logger.warning(
                    "Could not write to approval log %s: %s (suppressing further)",
                    self.log_path,
                    exc,
                )
            return False

    def close(self) -> None:
        """Close the underlying file handler if open."""
        if self._handler is not None:
            self._handler.close()
            self._handler = None


__all__ = [
    "APPROVAL_LOG_BACKUP_COUNT",
    "APPROVAL_LOG_REL_PATH",
    "ApprovalLogger",
    "LogAnswer",
    "LogDecision",
    "LogSource",
    "LogTier",
    "MAX_APPROVAL_LOG_BYTES",
    "MAX_TARGET_CHARS",
    "REDACTED_PLACEHOLDER",
    "redact_secrets",
]
