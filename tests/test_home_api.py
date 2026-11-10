"""Home helpers used by the web door, tested without HTTP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from aldwin.core.settings import load_config
from aldwin.host.home_api import (
    SettingsError,
    apply_settings_patch,
    get_settings_view,
    list_conversation_metas,
    load_conversation_json,
)
from aldwin.host.sessions import SessionStore

OPENAI_KEY = "sk-test-0123456789abcdef"


def _store(home: Path) -> SessionStore:
    return SessionStore(home / "sessions")


def test_list_hides_conversations_with_no_turns(aldwin_home: Path) -> None:
    """TC-29: only conversations with a turn are listed."""
    store = _store(aldwin_home)
    kept = store.create_new()
    kept.turns = [{"text": "Hello", "skills": []}]
    store.save(kept, kept.memory, interrupted=False)
    store.create_new()

    rows = list_conversation_metas(aldwin_home)

    assert [row["id"] for row in rows] == [kept.meta.id]
    assert "has_turns" not in rows[0]


def test_load_returns_none_for_missing_corrupt_or_odd_ids(aldwin_home: Path) -> None:
    """TC-30: a missing, damaged or path-like id gives None, never an error."""
    sessions = aldwin_home / "sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / "2026-01-01_bad.json").write_text("{", encoding="utf-8")
    for session_id in ("2026-01-01_none", "2026-01-01_bad", "../config", ".."):
        assert load_conversation_json(session_id, aldwin_home) is None


def test_load_returns_the_file_as_saved(aldwin_home: Path) -> None:
    store = _store(aldwin_home)
    stored = store.create_new()
    on_disk = json.loads(stored.path.read_text("utf-8"))
    assert load_conversation_json(stored.meta.id, aldwin_home) == on_disk


def test_settings_view_never_contains_a_raw_key(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-31: file and environment keys appear masked only."""
    (aldwin_home / "secrets.yaml").write_text(
        yaml.safe_dump({"openai_api_key": OPENAI_KEY}), encoding="utf-8"
    )
    (aldwin_home / "secrets.yaml").chmod(0o600)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env-0123456789")

    view = get_settings_view(aldwin_home)

    text = json.dumps(view)
    assert OPENAI_KEY not in text and "tvly-env-0123456789" not in text
    rows = {(row["kind"], row["name"]): row for row in view["keys"]}
    assert rows[("provider", "openai")]["source"] == "file"
    assert rows[("search", "tavily")]["source"] == "env"


def test_patch_applies_allowed_fields_and_refuses_the_rest(aldwin_home: Path) -> None:
    """TC-32: allowed fields are written; a refused patch writes nothing."""
    view = apply_settings_patch(
        {"limits": {"max_steps": 20}, "check_for_updates": False}, aldwin_home
    )
    assert view["limits"]["max_steps"] == 20
    assert load_config(aldwin_home).check_for_updates is False

    before = (aldwin_home / "config.yaml").read_text("utf-8")
    with pytest.raises(SettingsError, match="theme"):
        apply_settings_patch(
            {"model": "openai/gpt-4o-mini", "theme": "light"}, aldwin_home
        )
    assert (aldwin_home / "config.yaml").read_text("utf-8") == before
