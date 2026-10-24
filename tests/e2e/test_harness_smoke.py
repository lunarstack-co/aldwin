"""E2E harness smoke: skip without key; spawn `/help` when key + binary exist."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from e2e.harness import AldwinSpawn, write_transcript
from e2e.workspace_builder import build_e2e_workspace

pytestmark = pytest.mark.e2e


def test_e2e_skips_without_openai_api_key() -> None:
    """When OPENAI_API_KEY is unset, the e2e suite skips with a clear message."""
    if os.environ.get("OPENAI_API_KEY", "").strip():
        pytest.skip("key is set; this case only runs when unset")
    # Already in-process with no key: the session fixture path is covered by collection.
    # Also prove a subprocess with a cleared env skips cleanly.
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    env["OPENAI_API_KEY"] = ""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/e2e/test_refusals.py::test_refusals_openai",
            "-m",
            "e2e",
            "-q",
            "--tb=no",
            "-o",
            "addopts=",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "skipped" in out.lower() or "OPENAI_API_KEY" in out


def test_spawn_help_and_exit(tmp_path: Path, openai_api_key: str) -> None:
    """Spawn installed `aldwin`, send `/help`, exit; write transcript."""
    if shutil.which("aldwin") is None:
        pytest.skip("aldwin binary not on PATH; install with `uv tool install -e .`")
    home = tmp_path / "aldwin-home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model: gpt-4.1-mini\nprovider: openai\n",
        encoding="utf-8",
    )
    ws = build_e2e_workspace(tmp_path / "workspace")
    spawn = AldwinSpawn(home, ws, env={"OPENAI_API_KEY": openai_api_key})
    try:
        spawn.child.expect(r"› |You:|> ", timeout=30)
        spawn.send_line("/help")
        spawn.child.expect(r"help|/exit|commands", timeout=30)
        text = spawn.wait_done(timeout=30)
    finally:
        spawn.close()
    path = write_transcript("harness_help", text)
    assert path.is_file()
    cleaned = path.read_text(encoding="utf-8")
    assert "sk-" not in cleaned or "‹redacted›" in cleaned
