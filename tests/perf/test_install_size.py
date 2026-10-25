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


def _is_real_file(path: Path) -> bool:
    """True for a regular file, false for a directory or a symlink.

    `uv` symlinks the interpreter (`bin/python`, `bin/python3`, ...) to a
    Python build it manages outside the install entirely, shared across
    every tool it installs. `Path.is_file()`/`Path.stat()` follow symlinks,
    so counting those names would attribute that external, shared
    interpreter's size to this install, once per symlink name pointing to it.
    """
    return path.is_file() and not path.is_symlink()


def _dir_size_mb(root: Path) -> float:
    total = sum(f.stat().st_size for f in root.rglob("*") if _is_real_file(f))
    return total / (1024 * 1024)


def _size_breakdown(root: Path, limit: int = 20) -> str:
    """Return the largest entries under `root`, grouped by their first five
    path components (deep enough to separate individual packages inside
    site-packages from each other), descending by size."""
    totals: dict[str, int] = {}
    for f in root.rglob("*"):
        if not _is_real_file(f):
            continue
        key = "/".join(f.relative_to(root).parts[:5])
        totals[key] = totals.get(key, 0) + f.stat().st_size
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return "\n".join(f"{size / (1024 * 1024):8.2f} MB  {name}" for name, size in ranked)


def test_installed_size_under_budget(tmp_path: Path) -> None:
    """TC-08/TC-25: the wheel installs, without extras, at or under 120 MB."""
    _linux_only()
    uv_bin = shutil.which("uv")
    assert uv_bin is not None, "Expected `uv` binary on PATH"

    # Cache and tool dir must share a filesystem so `uv` can hardlink cached
    # package files into the install (as it does for a real user, whose cache
    # and tool directories both live under $HOME). A CI job's own UV_CACHE_DIR
    # can sit on a different mount than pytest's tmp_path, which forces uv to
    # fall back to full copies and measures an inflated, unrepresentative size.
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_TOOL_DIR": str(tmp_path / "tool"),
        "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
    }

    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        [uv_bin, "build", "--out-dir", str(dist_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert build.returncode == 0, f"uv build failed:\n{build.stderr}\n{build.stdout}"
    wheels = list(dist_dir.glob("aldwin-*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel, found {wheels}"

    tool_dir = tmp_path / "tool"
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
        f"{INSTALLED_SIZE_BUDGET_MB} MB budget\n"
        f"Largest entries:\n{_size_breakdown(tool_dir)}"
    )
