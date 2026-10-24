"""denial contract texts and 3-denial circuit breaker."""

from __future__ import annotations

from pathlib import Path

import pytest

from aldwin.core.policy import (
    CIRCUIT_BREAKER_LIMIT,
    DENIAL_TEXT,
    Decision,
    PolicySession,
    format_denial_text,
)
from aldwin.core.workspace import Workspace


@pytest.fixture
def session(tmp_path: Path, aldwin_home: Path) -> PolicySession:
    root = tmp_path / "ws"
    root.mkdir()
    return PolicySession(workspace=Workspace(root, home=aldwin_home), home=aldwin_home)


def test_denial_texts_exact() -> None:
    """Exact DENIAL_TEXT strings for user / edit / no_approver / error."""
    user, _ = format_denial_text(
        Decision(kind="ask", source="user", reason="declined"),
        answer_source="user",
    )
    assert user == DENIAL_TEXT["user"]

    edit, _ = format_denial_text(
        Decision(kind="diff", source="user", reason="declined"),
        answer_source="user",
        is_edit=True,
    )
    assert edit == DENIAL_TEXT["user_edit"]
    assert "The file was not changed." in edit

    no_one, _ = format_denial_text(
        Decision(kind="ask", source="user", reason="declined"),
        answer_source="no_approver",
    )
    assert no_one == DENIAL_TEXT["no_approver"]

    err, _ = format_denial_text(Decision(kind="deny", source="error", reason="boom"))
    assert err == DENIAL_TEXT["policy_error"]


def test_user_reason_truncated_to_280() -> None:
    """User reason after `n` is cut to 280 characters."""
    long = "x" * 400
    text, _ = format_denial_text(
        Decision(kind="ask", source="user", reason="declined"),
        user_reason=long,
        answer_source="user",
    )
    assert long[:280] in text
    assert long not in text


def test_circuit_breaker_three_denials(session: PolicySession) -> None:
    """three denials stop the turn with the fixed circuit-breaker text."""
    called: list[str] = []
    session.on_circuit_breaker = lambda: called.append("stop")
    for _ in range(CIRCUIT_BREAKER_LIMIT - 1):
        text, _ = session.record_denial(base_text="no", base_summary="no")
        assert text != DENIAL_TEXT["circuit_breaker"]
    text, summary = session.record_denial(base_text="no", base_summary="no")
    assert called == ["stop"]
    assert text == DENIAL_TEXT["circuit_breaker"]
    assert summary == DENIAL_TEXT["circuit_breaker"]
    session.reset_turn()
    assert session.turn_denials == 0
