"""`run_command` clean environment scrubbing."""

from __future__ import annotations

from pathlib import Path

import pytest
from canary import CANARY

from aldwin.core.policy import clean_env
from aldwin.core.settings import load_config


def test_env_scrub_strips_secrets_and_keeps_baseline() -> None:
    """secret-shaped vars absent; PATH/HOME/LANG kept."""
    raw = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/Users/fola",
        "LANG": "en_GB.UTF-8",
        "OPENAI_API_KEY": "sk-secret",
        "GITHUB_TOKEN": "ghp_x",
        "AWS_SECRET_ACCESS_KEY": "aws",
        "MY_PASSWORD": "pw",
        CANARY: "1",
    }
    cleaned = clean_env(raw, cwd=Path("/tmp/work"))
    for bad in (
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "MY_PASSWORD",
        CANARY,
    ):
        assert bad not in cleaned
    assert cleaned["PATH"] == "/usr/bin:/bin"
    assert cleaned["HOME"] == "/Users/fola"
    assert cleaned["LANG"] == "en_GB.UTF-8"


def test_env_allow_secret_still_scrubbed(
    aldwin_home: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`env_allow` listing a secret still strips it and warns; safe vars pass."""
    raw = {
        "PATH": "/bin",
        "HOME": "/tmp",
        "LANG": "C",
        "GITHUB_TOKEN": "ghp_x",
        "MY_VAR": "ok",
    }
    cleaned = clean_env(raw, env_allow=["GITHUB_TOKEN", "MY_VAR"])
    assert "GITHUB_TOKEN" not in cleaned
    assert cleaned.get("MY_VAR") == "ok"
    assert any("GITHUB_TOKEN" in r.message for r in caplog.records)

    (aldwin_home / "config.yaml").write_text(
        "model: gpt-4o\nrun_command:\n  env_allow:\n    - MY_VAR\n    - GITHUB_TOKEN\n",
        encoding="utf-8",
    )
    cfg = load_config(aldwin_home)
    assert "GITHUB_TOKEN" not in cfg.run_command.env_allow
    assert "MY_VAR" in cfg.run_command.env_allow


def test_connector_env_scrubs_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local server only gets the safe baseline when it names nothing."""
    from aldwin.core.connectors.config import ConnectorConfig
    from aldwin.core.connectors.local import build_server_env

    environ = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "LANG": "en_GB.UTF-8",
        "OPENAI_API_KEY": "sk-x",
        "GITHUB_TOKEN": "ghp_x",
        "AWS_SECRET_ACCESS_KEY": "aws-x",
        "MY_PASSWORD": "pw-x",
        CANARY: "1",
    }
    config = ConnectorConfig(name="x", command="npx", args=["pkg@1"])
    env = build_server_env(config, environ=environ, stored={}, cwd=tmp_path)
    assert not {
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "MY_PASSWORD",
        CANARY,
    } & set(env)
    assert {"PATH", "HOME", "LANG"} <= set(env)
