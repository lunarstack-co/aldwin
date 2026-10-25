"""Tests for the idempotent 0.0.1 -> 0.0.2 home migration."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from aldwin.core.home import (
    CONFIG_BACKUP_0_0_1_NAME,
    HOME_VERSION,
    HomeError,
    ensure_home,
    migrate_0_0_1,
)
from aldwin.core.memory import remember_note
from aldwin.core.memory_index import default_memory_index
from aldwin.core.settings import load_config

LEGACY_CONFIG = """\
# Aldwin configuration (~/.aldwin/config.yaml)
# A hand-written comment the user added.
model: gpt-4o-mini
theme: light
check_for_updates: true
"""


def _home_version_text(home: Path) -> str:
    return (home / "state" / "home_version").read_text(encoding="utf-8").strip()


def _backups(home: Path) -> list[Path]:
    return sorted(home.glob(f"{CONFIG_BACKUP_0_0_1_NAME}*"))


@pytest.fixture
def legacy_home(tmp_path: Path) -> Path:
    """A synthetic pre-0.0.2 home: a loosely-permissioned dir with only config.yaml."""
    root = tmp_path / "legacy"
    root.mkdir(mode=0o755)
    config_path = root / "config.yaml"
    config_path.write_text(LEGACY_CONFIG, encoding="utf-8")
    config_path.chmod(0o644)
    return root


@pytest.mark.uninitialized_home
def test_fresh_home_skips_migration(aldwin_home: Path) -> None:
    """TC-06: a brand-new 0.0.2 home is marked migrated with no 0.0.1 side effects."""
    created = ensure_home(aldwin_home)
    assert created is True

    migrate_0_0_1(aldwin_home, fresh_install=created)

    assert _home_version_text(aldwin_home) == str(HOME_VERSION)
    assert _backups(aldwin_home) == []


def test_upgrade_from_0_0_1_preserves_and_adds(legacy_home: Path) -> None:
    """TC-07: upgrading a synthetic 0.0.1 home backs up config.yaml and adds keys."""
    before = (legacy_home / "config.yaml").read_text(encoding="utf-8")

    created = ensure_home(legacy_home)
    assert created is False

    stale_log = legacy_home / "logs" / "aldwin.log.1"
    stale_log.write_text("old log line\n", encoding="utf-8")
    stale_log.chmod(0o644)

    migrate_0_0_1(legacy_home, fresh_install=created)

    backups = _backups(legacy_home)
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == before

    assert (stale_log.stat().st_mode & 0o777) == 0o600

    after = (legacy_home / "config.yaml").read_text(encoding="utf-8")
    assert "# A hand-written comment the user added." in after
    assert "model: gpt-4o-mini" in after
    assert "theme: light" in after
    assert "run_command:" in after
    assert "env_allow: []" in after
    assert "workspace" not in after

    config = load_config(legacy_home)
    assert config.model == "gpt-4o-mini"
    assert config.run_command.env_allow == []

    for rel in ("tmp", "state", "logs"):
        mode = (legacy_home / rel).stat().st_mode & 0o777
        assert mode == 0o700

    assert (legacy_home / "memory" / "index.sqlite").is_file()
    assert _home_version_text(legacy_home) == str(HOME_VERSION)


def test_second_run_up_to_date(
    legacy_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """TC-08: a second migration run makes no further changes and logs as much."""
    ensure_home(legacy_home)
    migrate_0_0_1(legacy_home, fresh_install=False)

    config_path = legacy_home / "config.yaml"
    config_mtime = config_path.stat().st_mtime_ns
    backup_mtime = _backups(legacy_home)[0].stat().st_mtime_ns

    with caplog.at_level(logging.INFO, logger="aldwin.core.home"):
        migrate_0_0_1(legacy_home, fresh_install=False)

    assert "migration: up to date" in caplog.text
    assert len(_backups(legacy_home)) == 1
    assert config_path.stat().st_mtime_ns == config_mtime
    assert _backups(legacy_home)[0].stat().st_mtime_ns == backup_mtime


def test_backup_write_failure_leaves_config_untouched(
    legacy_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backup-write failure fails migration cleanly; nothing else changes.

    `_ensure_dir` auto-repairs a merely-chmodded home directory before any write
    (by design, for a stale 0.0.1 install), so a real permission failure is
    simulated by making the backup write itself raise, matching "the backup
    write fails first, before any other mutation".
    """
    ensure_home(legacy_home)
    before = (legacy_home / "config.yaml").read_text(encoding="utf-8")

    import aldwin.core.home as home_module

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(home_module, "atomic_write", _boom)

    with pytest.raises(
        HomeError, match=r"^migration failed: .+; your data is unchanged$"
    ):
        migrate_0_0_1(legacy_home, fresh_install=False)

    assert (legacy_home / "config.yaml").read_text(encoding="utf-8") == before
    assert _backups(legacy_home) == []


def test_crash_recovery_completes(legacy_home: Path) -> None:
    """TC-10: a pre-existing backup (simulated crash after step 1) still finishes."""
    original = (legacy_home / "config.yaml").read_text(encoding="utf-8")
    (legacy_home / CONFIG_BACKUP_0_0_1_NAME).write_text(original, encoding="utf-8")

    ensure_home(legacy_home)
    migrate_0_0_1(legacy_home, fresh_install=False)

    backups = _backups(legacy_home)
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    assert "run_command:" in (legacy_home / "config.yaml").read_text(encoding="utf-8")
    assert _home_version_text(legacy_home) == str(HOME_VERSION)


def test_index_built_and_corrupt_session_skipped(legacy_home: Path) -> None:
    """TC-11: migration builds the memory index; a corrupt session file is skipped."""
    ensure_home(legacy_home)
    remember_note(legacy_home, "buy more coffee filters")
    sessions_dir = legacy_home / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "broken.json").write_text("{not json", encoding="utf-8")

    migrate_0_0_1(legacy_home, fresh_install=False)

    hits = default_memory_index(legacy_home).search_notes_all_time("coffee filters")
    assert any("coffee filters" in hit["text"] for hit in hits)
