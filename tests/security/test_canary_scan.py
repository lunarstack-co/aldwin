"""canary never leaks into events, logs, tmp, notes, or sessions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from canary import CANARY, scan_for_canary

from security._helpers import call_fail


async def test_canary_absent_after_security_actions(
    security_tools: SimpleNamespace,
    canary_home: Path,
    set_answer,
) -> None:
    """after blocked reads/greps/env, canary is absent from artifacts."""
    security_tools.session.reset_turn()
    await call_fail(
        security_tools.tools["read_file"],
        {"path": ".env"},
    )
    security_tools.session.reset_turn()
    grepped = await security_tools.tools["grep"].call({"pattern": CANARY, "path": "."})
    assert ".env:" not in str(grepped)

    set_answer("yes")
    env_out = await security_tools.tools["run_command"].call({"command": "env"})
    assert CANARY not in str(env_out)

    paths = [
        security_tools.home / "logs" / "approvals.jsonl",
        security_tools.home / "logs",
        security_tools.home / "tmp",
        security_tools.home / "memory",
        security_tools.home / "sessions",
    ]
    texts = [str(grepped), str(env_out)]
    hits = scan_for_canary(paths, texts)
    # Canary may legitimately live in secrets.yaml / .env on disk (planted).
    # It must not appear in logs, tmp, tool output, or sessions.
    env_path = str(security_tools.root / ".env")
    bad = [
        h
        for h in hits
        if "secrets.yaml" not in h and "/.env" not in h and h != env_path
    ]
    assert bad == [], bad
