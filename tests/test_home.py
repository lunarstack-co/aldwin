"""Unit tests for aldwin.core.home."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from aldwin.core.home import (
    STARTER_ASSETS,
    SUBDIRS,
    atomic_write,
    ensure_home,
    format_home_path,
    home_dir,
    read_base_instructions,
)


def test_home_dir_defaults_to_dot_aldwin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_user_home = tmp_path / "user"
    fake_user_home.mkdir(exist_ok=True)
    monkeypatch.delenv("ALDWIN_HOME", raising=False)
    monkeypatch.setenv("HOME", str(fake_user_home))

    assert home_dir() == (fake_user_home / ".aldwin").resolve()


def test_home_dir_uses_aldwin_home_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ALDWIN_HOME overrides ~/.aldwin and leaves ~/.aldwin untouched."""
    fake_user_home = tmp_path / "fake_user"
    fake_user_home.mkdir(exist_ok=True)
    custom_home = tmp_path / "custom"
    monkeypatch.setenv("HOME", str(fake_user_home))
    monkeypatch.setenv("ALDWIN_HOME", str(custom_home))

    assert home_dir() == custom_home.resolve()
    created = ensure_home()
    assert created is True
    assert custom_home.is_dir()
    assert not (fake_user_home / ".aldwin").exists()


def test_home_dir_expands_tilde_and_ignores_blank_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Home_dir() expands ~ and falls back on whitespace-only ALDWIN_HOME."""
    fake_user_home = tmp_path / "user"
    fake_user_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_user_home))

    monkeypatch.setenv("ALDWIN_HOME", "~/my-aldwin")
    assert home_dir() == (fake_user_home / "my-aldwin").resolve()

    monkeypatch.setenv("ALDWIN_HOME", "   ")
    assert home_dir() == (fake_user_home / ".aldwin").resolve()


def test_format_home_path_shortens_under_user_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_user_home = tmp_path / "user"
    fake_user_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_user_home))

    assert format_home_path(fake_user_home / ".aldwin") == "~/.aldwin"
    assert format_home_path(fake_user_home) == "~"
    outside = tmp_path / "other" / "aldwin"
    assert format_home_path(outside) == str(outside.resolve())


def test_atomic_write_writes_text_and_bytes_with_mode(aldwin_home: Path) -> None:
    text_target = aldwin_home / "config.yaml"
    atomic_write(text_target, "model: gpt-4o\n", mode=0o600)

    assert (aldwin_home.stat().st_mode & 0o777) == 0o700
    assert text_target.read_text(encoding="utf-8") == "model: gpt-4o\n"
    assert (text_target.stat().st_mode & 0o777) == 0o600

    bytes_target = aldwin_home / "raw.bin"
    atomic_write(bytes_target, b"\x00\x01\x02", mode=0o640)
    assert bytes_target.read_bytes() == b"\x00\x01\x02"
    assert (bytes_target.stat().st_mode & 0o777) == 0o640


def test_atomic_write_cleans_up_temp_file_on_error(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interrupted atomic write leaves original file intact and no .tmp file."""
    target = aldwin_home / "config.yaml"
    atomic_write(target, "model: gpt-4o\n")

    def fail_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError("simulated disk error during replace")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated disk error"):
        atomic_write(target, "model: broken\n")

    assert target.read_text(encoding="utf-8") == "model: gpt-4o\n"
    assert list(aldwin_home.glob("*.tmp")) == []
    assert list(aldwin_home.glob(".*.tmp")) == []


@pytest.mark.uninitialized_home
def test_ensure_home_creates_subdirs_with_0700_and_copies_assets(
    aldwin_home: Path,
) -> None:
    """First call creates folders/assets; second is a no-op."""
    assert not aldwin_home.exists()

    first = ensure_home()
    assert first is True
    assert (aldwin_home.stat().st_mode & 0o777) == 0o700

    for rel_dir in SUBDIRS:
        folder = aldwin_home / rel_dir
        assert folder.is_dir(), f"Missing directory {rel_dir}"
        assert (folder.stat().st_mode & 0o777) == 0o700

    for _, dest_rel in STARTER_ASSETS:
        dest = aldwin_home / dest_rel
        assert dest.is_file(), f"Missing starter asset {dest_rel}"
        assert (dest.stat().st_mode & 0o777) == 0o600
        assert (dest.parent.stat().st_mode & 0o777) == 0o700

    second = ensure_home()
    assert second is False


def test_ensure_home_preserves_user_edits_and_adds_missing_assets(
    aldwin_home: Path,
) -> None:
    """User edit to persona.md is kept; defaults are not copied into skills/."""
    ensure_home()
    persona_path = aldwin_home / "persona.md"
    skill_path = aldwin_home / "skills" / "using-aldwin" / "SKILL.md"

    persona_path.write_text("Custom persona\n", encoding="utf-8")
    assert not skill_path.exists()

    created = ensure_home()
    assert created is False
    assert persona_path.read_text(encoding="utf-8") == "Custom persona\n"
    # Packaged defaults stay in the package; ensure_home never restores them.
    assert not skill_path.exists()


def test_ensure_home_repairs_loose_directory_permissions(
    aldwin_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Folder found with mode 0755 is repaired to 0700 with a log warning."""
    ensure_home()
    sessions_dir = aldwin_home / "sessions"
    secrets_file = aldwin_home / "secrets.yaml"
    atomic_write(secrets_file, "openai_api_key: sk-test\n", mode=0o600)

    os.chmod(aldwin_home, 0o755)
    os.chmod(sessions_dir, 0o755)
    os.chmod(secrets_file, 0o644)

    with caplog.at_level(logging.WARNING, logger="aldwin.core.home"):
        created = ensure_home()

    assert created is False
    assert (aldwin_home.stat().st_mode & 0o777) == 0o700
    assert (sessions_dir.stat().st_mode & 0o777) == 0o700
    assert (secrets_file.stat().st_mode & 0o777) == 0o600
    assert "0755" in caplog.text
    assert "0644" in caplog.text


def test_base_instructions_stay_in_package(aldwin_home: Path) -> None:
    """Base instructions stay inside package data and are not copied."""
    ensure_home()
    instructions = read_base_instructions()
    assert "Aldwin" in instructions
    assert not (aldwin_home / "instructions.md").exists()


@pytest.mark.uninitialized_home
def test_ensure_home_concurrent_calls_succeed(aldwin_home: Path) -> None:
    """Concurrent ensure_home() calls complete without race errors."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ensure_home(), range(8)))

    assert any(results)
    for _, dest_rel in STARTER_ASSETS:
        dest = aldwin_home / dest_rel
        assert dest.is_file()
        assert (dest.stat().st_mode & 0o777) == 0o600
