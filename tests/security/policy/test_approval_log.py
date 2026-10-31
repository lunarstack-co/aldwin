"""Unit tests for `aldwin.core.approval_log`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aldwin.core.approval_log import (
    REDACTED_PLACEHOLDER,
    ApprovalLogger,
    redact_secrets,
)


def test_approval_log_records_all_decisions_and_redacts_secrets(
    aldwin_home: Path,
) -> None:
    """`approvals.jsonl` writes 11 fields, mode `0600`, and redacts secrets."""
    logger = ApprovalLogger(
        home=aldwin_home,
        session_id_getter=lambda: "s-3f1a",
    )

    # 1. Auto read
    assert logger.log_decision(
        call_id="c-1",
        tool="read_file",
        target="README.md",
        digest=None,
        tier="auto",
        decision="auto",
        answer="none",
        source="rule",
        reason="auto tier",
    )

    # 2. Blocked path
    assert logger.log_decision(
        call_id="c-2",
        tool="read_file",
        target="~/.ssh/id_rsa",
        digest=None,
        tier="auto",
        decision="blocked",
        answer="none",
        source="rule",
        reason="blocked: ~/.ssh/id_rsa may contain secrets",
    )

    # 3. Session grant
    assert logger.log_decision(
        call_id="c-3",
        tool="edit_file",
        target="notes.md",
        digest=None,
        tier="diff",
        decision="auto",
        answer="none",
        source="grant",
        reason="granted file: notes.md",
    )

    # 4. User said no with reason + secret in command
    secret_cmd = (
        "OPENAI_API_KEY=sk-1234567890abcdefghijklmno "
        "curl -H 'Bearer ghp_1234567890abcdefghijklmnopqrstuvwxyz1234' "
        "AKIA1234567890ABCDEF "
        "xoxb-123456789012-abcdef "
        "tvly-dev-1234567890abcdef "
        "-----BEGIN RSA PRIVATE KEY-----"
    )
    assert logger.log_decision(
        call_id="c-4",
        tool="run_command",
        target=secret_cmd,
        digest="sha256:9f2c",
        tier="ask",
        decision="asked",
        answer="no",
        source="user",
        reason="only fix the heading sk-1234567890abcdefghijklmno",
    )
    logger.close()

    log_path = aldwin_home / "logs" / "approvals.jsonl"
    assert log_path.is_file()
    assert (log_path.stat().st_mode & 0o777) == 0o600

    raw_text = log_path.read_text(encoding="utf-8")
    assert "sk-1234567890" not in raw_text
    assert "ghp_1234567890" not in raw_text
    assert "AKIA1234567890" not in raw_text
    assert "xoxb-123456789012" not in raw_text
    assert "tvly-dev-1234567890" not in raw_text
    assert "BEGIN RSA PRIVATE KEY" not in raw_text

    records = [json.loads(line) for line in raw_text.splitlines()]
    assert len(records) == 4

    expected_keys = {
        "ts",
        "session",
        "call_id",
        "tool",
        "target",
        "digest",
        "tier",
        "decision",
        "answer",
        "source",
        "reason",
        "connector",
    }
    for rec in records:
        assert set(rec.keys()) == expected_keys
        assert rec["session"] == "s-3f1a"

    assert (records[0]["decision"], records[0]["answer"], records[0]["source"]) == (
        "auto",
        "none",
        "rule",
    )
    assert (records[1]["decision"], records[1]["answer"], records[1]["source"]) == (
        "blocked",
        "none",
        "rule",
    )
    assert (records[2]["decision"], records[2]["answer"], records[2]["source"]) == (
        "auto",
        "none",
        "grant",
    )
    assert (records[3]["decision"], records[3]["answer"], records[3]["source"]) == (
        "asked",
        "no",
        "user",
    )
    assert REDACTED_PLACEHOLDER in records[3]["target"]
    assert REDACTED_PLACEHOLDER in records[3]["reason"]


def test_target_truncation_and_rotation(aldwin_home: Path) -> None:
    """Target is capped at 512 chars and log rotates at `max_bytes`."""
    logger = ApprovalLogger(
        home=aldwin_home,
        max_bytes=450,
        backup_count=2,
    )
    long_target = "x" * 700
    for i in range(4):
        assert logger.log_decision(
            call_id=f"c-{i}",
            tool="read_file",
            target=long_target,
            digest=None,
            tier="auto",
            decision="auto",
            answer="none",
            source="rule",
        )
    logger.close()

    log_path = aldwin_home / "logs" / "approvals.jsonl"
    assert log_path.is_file()
    assert (aldwin_home / "logs" / "approvals.jsonl.1").is_file()
    latest = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert len(latest["target"]) == 512
    assert latest["target"].endswith("…")
    assert redact_secrets("") == ""


def test_unwritable_log_dir_warns_once_and_does_not_block_tools(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Logging failure warns once per session and never raises."""

    def _boom_session() -> str:
        raise RuntimeError("session getter failed")

    logger = ApprovalLogger(home=aldwin_home, session_id_getter=_boom_session)

    def _fail_handler() -> object:
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(logger, "_ensure_handler", _fail_handler)

    ok1 = logger.log_decision(
        call_id="c-1",
        tool="read_file",
        target="README.md",
        digest=None,
        tier="auto",
        decision="auto",
        answer="none",
        source="rule",
    )
    ok2 = logger.log_decision(
        call_id="c-2",
        tool="read_file",
        target="README.md",
        digest=None,
        tier="auto",
        decision="auto",
        answer="none",
        source="rule",
    )

    assert ok1 is False
    assert ok2 is False
    warnings = [
        r for r in caplog.records if "Could not write to approval log" in r.message
    ]
    assert len(warnings) == 1
