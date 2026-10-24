"""0.0.1 to 0.0.2 upgrade preserves notes and persona under an isolated tool dir."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def test_upgrade_001_to_002(tmp_path: Path) -> None:
    """Install 0.0.1, upgrade to the local wheel, assert notes/persona intact."""
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")

    tool_dir = tmp_path / "uv-tools"
    tool_dir.mkdir()
    home = tmp_path / "aldwin-home"
    home.mkdir()
    env = {
        **os.environ,
        "UV_TOOL_DIR": str(tool_dir),
        "ALDWIN_HOME": str(home),
        "HOME": str(tmp_path / "user"),
    }
    Path(env["HOME"]).mkdir(exist_ok=True)

    bin_dir = tmp_path / "uv-bin"
    bin_dir.mkdir()
    env["UV_TOOL_BIN_DIR"] = str(bin_dir)
    install = subprocess.run(
        ["uv", "tool", "install", "aldwin==0.0.1"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if install.returncode != 0:
        pytest.skip(f"could not install aldwin==0.0.1: {install.stderr[-500:]}")

    aldwin_bin = bin_dir / "aldwin"
    if not aldwin_bin.is_file():
        candidates = list(tool_dir.rglob("aldwin"))
        aldwin_bin = candidates[0] if candidates else aldwin_bin
    if not aldwin_bin.is_file():
        pytest.skip(
            "aldwin binary missing after 0.0.1 install: "
            f"stdout={install.stdout[-300:]!r} stderr={install.stderr[-300:]!r}"
        )

    notes = home / "memory" / "notes"
    notes.mkdir(parents=True)
    note = notes / "seed.md"
    note.write_text("keep me\n", encoding="utf-8")
    persona = home / "persona.md"
    persona.write_text("You are Aldwin.\n", encoding="utf-8")
    note_bytes = note.read_bytes()
    persona_bytes = persona.read_bytes()

    root = Path(__file__).resolve().parents[2]
    build = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(tmp_path / "dist")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if build.returncode != 0:
        pytest.fail(build.stderr[-800:])
    wheels = list((tmp_path / "dist").glob("aldwin-*.whl"))
    assert wheels, "local wheel missing"
    upgrade = subprocess.run(
        ["uv", "tool", "install", "--force", str(wheels[0])],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if upgrade.returncode != 0:
        pytest.fail(upgrade.stderr[-800:])

    assert note.read_bytes() == note_bytes
    assert persona.read_bytes() == persona_bytes
