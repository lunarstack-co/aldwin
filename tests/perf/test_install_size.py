"""Installed package size budget (Linux CI; `@pytest.mark.perf`)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.perf

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLED_SIZE_BUDGET_MB: int = 120


def _linux_only() -> None:
    if sys.platform != "linux":
        pytest.skip("Installed-size budget runs on Linux CI only")


def _dir_size_mb(root: Path) -> float:
    total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def test_installed_size_under_budget(tmp_path: Path) -> None:
    """TC-08/TC-25: the wheel installs, without extras, at or under 120 MB."""
    _linux_only()
    uv_bin = shutil.which("uv")
    assert uv_bin is not None, "Expected `uv` binary on PATH"

    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        [uv_bin, "build", "--out-dir", str(dist_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, f"uv build failed:\n{build.stderr}\n{build.stdout}"
    wheels = list(dist_dir.glob("aldwin-*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel, found {wheels}"

    tool_dir = tmp_path / "tool"
    bin_dir = tmp_path / "bin"
    env = {**os.environ, "UV_TOOL_DIR": str(tool_dir), "UV_TOOL_BIN_DIR": str(bin_dir)}
    install = subprocess.run(
        [uv_bin, "tool", "install", str(wheels[0])],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert install.returncode == 0, f"uv tool install failed:\n{install.stderr}"

    size_mb = _dir_size_mb(tool_dir)
    assert size_mb <= INSTALLED_SIZE_BUDGET_MB, (
        f"Installed size {size_mb:.1f} MB exceeds the "
        f"{INSTALLED_SIZE_BUDGET_MB} MB budget"
    )
