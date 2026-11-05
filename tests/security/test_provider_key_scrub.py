"""Security canary tests verifying cloud provider keys never leak."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import collect
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.policy import clean_env
from aldwin.host.runner import PASTED_SECRET_NOTICE
from aldwin.protocol import REDACTED, NoticeEvent

# Synthetic canary keys for each first-ship provider
CANARY_GEMINI = "AIza" + "CanaryGeminiKey99" + "x" * 18  # 39 chars total
CANARY_OPENAI = "sk-proj-" + "CanaryOpenAiKey99" + "x" * 20
CANARY_ANTHROPIC = "sk-ant-api03-" + "CanaryAnthropicKey99" + "x" * 20
CANARY_OPENROUTER = "sk-or-v1-" + "CanaryOpenRouterKey99" + "x" * 30
CANARY_DEEPSEEK = "sk-" + "CanaryDeepSeek99" + "x" * 16  # 35 chars total

ALL_CANARIES = (
    CANARY_GEMINI,
    CANARY_OPENAI,
    CANARY_ANTHROPIC,
    CANARY_OPENROUTER,
    CANARY_DEEPSEEK,
)


@pytest.mark.parametrize("canary", ALL_CANARIES)
async def test_cloud_canaries_pasted_in_chat_never_reach_model_or_session(
    host_client: AldwinClient,
    fake_model: FakeModel,
    aldwin_home: Path,
    canary: str,
) -> None:
    """Cloud keys pasted in chat are warned, redacted, and saved as redacted."""
    fake_model.add_turns(FakeTurn.text(["I have processed your message."]))
    user_line = f"Here is my key: {canary} please use it."

    events = await collect(host_client.turn(user_line))

    # Notice was emitted
    notices = [ev.message for ev in events if isinstance(ev, NoticeEvent)]
    assert PASTED_SECRET_NOTICE in notices

    # Model was given redacted text, never the plaintext canary
    sent_to_model = [
        message.content or "" for call in fake_model.calls for message in call
    ]
    for msg in sent_to_model:
        assert canary not in msg
        if "Here is my key:" in msg:
            assert REDACTED in msg

    # Session files never contain the canary
    session_files = list((aldwin_home / "sessions").glob("*.json"))
    assert len(session_files) > 0

    expected_text = f"Here is my key: {REDACTED} please use it."
    for sess in session_files:
        raw = sess.read_text(encoding="utf-8")
        assert canary not in raw
        decoded = json.loads(raw)
        assert decoded["turns"][0]["text"] == expected_text


def test_canaries_never_leak_into_approval_log(aldwin_home: Path) -> None:
    """Approval logs redact cloud canaries in commands, targets, and reasons."""
    logger = ApprovalLogger(home=aldwin_home, session_id_getter=lambda: "sess-sec-1")

    for idx, canary in enumerate(ALL_CANARIES):
        logger.log_decision(
            call_id=f"c-{idx}",
            tool="run_command",
            target=f"curl -H 'Authorization: Bearer {canary}' https://api.example.com",
            digest=None,
            tier="ask",
            decision="asked",
            answer="no",
            source="user",
            reason=f"refusing command containing {canary}",
        )

    log_path = aldwin_home / "logs" / "approvals.jsonl"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")

    for canary in ALL_CANARIES:
        assert canary not in content
    assert content.count(REDACTED) >= len(ALL_CANARIES) * 2


def test_canaries_never_leak_into_system_log(aldwin_home: Path) -> None:
    """Rotating system log redacts all first-ship cloud provider canaries."""
    import logging

    from aldwin.core.logging import setup_logging

    log_path = setup_logging(aldwin_home, debug=True)
    app_logger = logging.getLogger("aldwin.test_scrub")

    for canary in ALL_CANARIES:
        app_logger.warning("Simulated failure with key: %s", canary)

    log_content = log_path.read_text(encoding="utf-8")
    for canary in ALL_CANARIES:
        assert canary not in log_content


def test_clean_env_strips_all_provider_api_keys() -> None:
    """clean_env strips first-ship provider env vars and secret tokens."""
    dirty_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/Users/user",
        "SHELL": "/bin/zsh",
        "OPENAI_API_KEY": "sk-openai-1234",
        "ANTHROPIC_API_KEY": "sk-ant-1234",
        "GEMINI_API_KEY": "AIza-gemini-1234",
        "OPENROUTER_API_KEY": "sk-or-1234",
        "DEEPSEEK_API_KEY": "sk-ds-1234",
        "TAVILY_API_KEY": "tvly-1234",
        "CUSTOM_SERVER_TOKEN": "custom-tok-1234",
        "MY_SECRET_PASSWORD": "secret-pass-1234",
        "DB_CREDENTIALS": "user:pass@host",
    }

    cleaned = clean_env(dirty_env, env_allow=("OPENAI_API_KEY", "CUSTOM_SERVER_TOKEN"))

    for secret_name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "TAVILY_API_KEY",
        "CUSTOM_SERVER_TOKEN",
        "MY_SECRET_PASSWORD",
        "DB_CREDENTIALS",
    ):
        assert secret_name not in cleaned
    assert "PATH" in cleaned
    assert "HOME" in cleaned
