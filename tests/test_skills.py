"""Unit tests for `aldwin.core.skills` catalogue, lockdown, and migration."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from aldwin.core.skills import (
    DEFAULT_SKILL_NAMES,
    STARTER_001_BACKUP_FOLDER,
    STARTER_001_FOLDER,
    STARTER_001_HASH,
    build_catalogue,
    migrate_001_starter_skill,
    skills_fingerprint,
)


def _write_skill(
    folder: Path, name: str, description: str, body: str = "Step 1.\n"
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description!r}\n---\n\n{body}",
        encoding="utf-8",
    )


def test_tc01_fresh_home_ten_defaults(aldwin_home: Path) -> None:
    (aldwin_home / "skills").mkdir(exist_ok=True)
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    loaded = [e for e in cat if e.skill is not None]
    assert len(loaded) == 10
    assert {e.name for e in loaded} == set(DEFAULT_SKILL_NAMES)
    assert all(e.source == "default" for e in loaded)
    assert not any((aldwin_home / "skills" / n).exists() for n in DEFAULT_SKILL_NAMES)


def test_tc02_user_override_by_name(aldwin_home: Path) -> None:
    (aldwin_home / "skills").mkdir(exist_ok=True)
    _write_skill(
        aldwin_home / "skills" / "terminal",
        "terminal",
        "USER TERM Load when testing.",
    )
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    terminal = next(e for e in cat if e.name == "terminal")
    assert terminal.source == "user override"
    assert "USER TERM" in terminal.description


def test_tc04_tools_stripped_no_import(
    aldwin_home: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (aldwin_home / "skills").mkdir(exist_ok=True)
    folder = aldwin_home / "skills" / "fancy"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: fancy\ndescription: Fancy Load when testing.\n"
        'tools: ["os:system"]\n---\n\nBody.\n',
        encoding="utf-8",
    )
    import importlib

    import agentling.skills as agentling_skills

    real_import = importlib.import_module

    def _guarded_import(name: str, package: str | None = None) -> object:
        # `tools: ["os:system"]` would import module path `os`.
        if name == "os":
            raise AssertionError("import_module called for skill tools entry")
        if package is None:
            return real_import(name)
        return real_import(name, package=package)

    with (
        patch.object(
            agentling_skills.Skill,
            "load_tools",
            side_effect=AssertionError("load_tools called"),
        ),
        patch.object(
            agentling_skills,
            "_resolve_tool",
            side_effect=AssertionError("_resolve_tool called"),
        ),
        patch.object(importlib, "import_module", side_effect=_guarded_import),
        caplog.at_level(logging.WARNING, logger="aldwin.core.skills"),
    ):
        cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    fancy = next(e for e in cat if e.name == "fancy")
    assert fancy.skill is not None
    assert fancy.skill.tools == []
    assert "tools removed" in fancy.flags
    assert any("tools removed" in r.getMessage() for r in caplog.records)


def test_tc05_bad_yaml_error_row(aldwin_home: Path) -> None:
    (aldwin_home / "skills").mkdir(exist_ok=True)
    bad = aldwin_home / "skills" / "broken"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: broken\n---\nno desc\n", encoding="utf-8")
    _write_skill(
        aldwin_home / "skills" / "ok-skill",
        "ok-skill",
        "Ok Load when testing.",
    )
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    broken = next(e for e in cat if e.name == "broken")
    assert broken.skill is None
    assert any(f.startswith("error:") for f in broken.flags)
    assert any(e.name == "ok-skill" and e.skill for e in cat)


def test_tc14_using_aldwin_identity_render(aldwin_home: Path) -> None:
    (aldwin_home / "skills").mkdir(exist_ok=True)
    cat = build_catalogue(aldwin_home, version="9.9.9", model="gpt-special")
    body = next(e for e in cat if e.name == "using-aldwin").body
    assert "askaldwin.com" in body
    assert "github.com/lunarstack-co/aldwin" in body
    assert "pypi.org/project/aldwin" in body
    assert "9.9.9" in body
    assert "Apache-2.0" in body
    assert "not made by OpenAI or Anthropic" in body
    assert "gpt-special" in body


def test_tc15_001_starter_hash_rename(aldwin_home: Path) -> None:
    folder = aldwin_home / "skills" / STARTER_001_FOLDER
    folder.mkdir(parents=True)
    # The fixture is the 0.0.1 starter, so its bytes match STARTER_001_HASH.
    fixture = Path(__file__).parent / "fixtures" / "using-aldwin-001" / "SKILL.md"
    if not fixture.is_file():
        pytest.skip("0.0.1 starter fixture missing")
    data = fixture.read_bytes()
    assert hashlib.sha256(data).hexdigest() == STARTER_001_HASH
    (folder / "SKILL.md").write_bytes(data)
    assert migrate_001_starter_skill(aldwin_home) is True
    assert not folder.exists()
    assert (aldwin_home / "skills" / STARTER_001_BACKUP_FOLDER / "SKILL.md").is_file()
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    ua = next(e for e in cat if e.name == "using-aldwin")
    assert ua.source == "default"


def test_build_agent_rejects_reserved_skill_name(
    aldwin_home: Path,
) -> None:
    """User skills named like built-in tools are skipped on the live agent path."""
    from fakes.model import FakeModel

    from aldwin.core.agent import build_agent
    from aldwin.core.settings import AldwinConfig, SecretsConfig

    (aldwin_home / "skills").mkdir(exist_ok=True)
    _write_skill(
        aldwin_home / "skills" / "create_skill",
        "create_skill",
        "Hijack Load when testing.",
        "Should not load.\n",
    )

    async def _ask(_summary: str, _detail: str = "") -> bool:
        return True

    agent = build_agent(
        AldwinConfig(),
        SecretsConfig(),
        _ask,
        home=aldwin_home,
        model=FakeModel(),
        tools=[],
    )
    assert "create_skill" not in agent.skills
    invalid = getattr(agent, "invalid_skills", ())
    assert any(i.name == "create_skill" and "reserved" in i.reason for i in invalid)


def test_base_instructions_include_identity() -> None:
    from aldwin.core.agent import load_base_instructions

    text = load_base_instructions()
    assert "Folarin (Fola) Akinloye" in text
    assert "askaldwin.com" in text
    assert "not made by OpenAI or Anthropic" in text


def test_edited_001_starter_kept_as_override(aldwin_home: Path) -> None:
    folder = aldwin_home / "skills" / STARTER_001_FOLDER
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: using-aldwin\n"
        "description: Edited Load when testing.\n---\n\nMine.\n",
        encoding="utf-8",
    )
    assert migrate_001_starter_skill(aldwin_home) is False
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    ua = next(e for e in cat if e.name == "using-aldwin")
    assert ua.source == "user override"


def test_missing_skill_md_is_error_row(aldwin_home: Path) -> None:
    empty = aldwin_home / "skills" / "empty-skill"
    empty.mkdir(parents=True)
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    row = next(e for e in cat if e.name == "empty-skill")
    assert row.skill is None
    assert any("missing" in f for f in row.flags)


def test_duplicate_user_folders_prefer_matching_dirname(aldwin_home: Path) -> None:
    (aldwin_home / "skills").mkdir(exist_ok=True)
    _write_skill(
        aldwin_home / "skills" / "dup-a",
        "shared",
        "First Load when testing.",
    )
    _write_skill(
        aldwin_home / "skills" / "shared",
        "shared",
        "Winner Load when testing.",
    )
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    shared = [e for e in cat if e.name == "shared" and e.skill is not None]
    assert len(shared) == 1
    assert shared[0].path is not None
    assert shared[0].path.name == "shared"
    assert "Winner" in shared[0].description


def test_default_skill_text_renders_template() -> None:
    from aldwin.core.skills import default_skill_text

    rendered = default_skill_text("using-aldwin", version="1.2.3", model="gpt-x")
    assert "1.2.3" in rendered
    assert "gpt-x" in rendered
    plain = default_skill_text("files")
    assert "name: files" in plain


def test_oversized_user_skill_becomes_error(aldwin_home: Path) -> None:
    from aldwin.core.skills import MAX_DESCRIPTION_CHARS, MAX_USER_BODY_CHARS

    (aldwin_home / "skills").mkdir(exist_ok=True)
    long_body = "x" * (MAX_USER_BODY_CHARS + 10)
    _write_skill(
        aldwin_home / "skills" / "too-long",
        "too-long",
        "Too long Load when testing.",
        long_body + "\n",
    )
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    row = next(e for e in cat if e.name == "too-long")
    assert row.skill is None
    assert any("body too long" in f for f in row.flags)

    long_desc = "D" * (MAX_DESCRIPTION_CHARS + 5) + " Load when testing."
    _write_skill(
        aldwin_home / "skills" / "long-desc",
        "long-desc",
        long_desc,
    )
    cat2 = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    row2 = next(e for e in cat2 if e.name == "long-desc")
    assert row2.skill is None


def test_tools_null_in_frontmatter_ok(aldwin_home: Path) -> None:
    folder = aldwin_home / "skills" / "null-tools"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: null-tools\n"
        "description: Null tools Load when testing.\ntools: null\n---\n\nBody.\n",
        encoding="utf-8",
    )
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    entry = next(e for e in cat if e.name == "null-tools")
    assert entry.skill is not None
    assert entry.skill.tools == []


def test_invalid_frontmatter_variants(aldwin_home: Path) -> None:
    (aldwin_home / "skills").mkdir(exist_ok=True)
    cases = {
        "no-fm": "no frontmatter here\n",
        "bad-yaml": "---\nname: [unterminated\n---\n\nBody.\n",
        "not-map": "---\njust a string\n---\n\nBody.\n",
        "empty-name": "---\nname: ''\ndescription: X Load when.\n---\n\nBody.\n",
        "bad-tools": (
            "---\nname: bad-tools\ndescription: X Load when.\n"
            "tools: not-a-list\n---\n\nBody.\n"
        ),
        "name-mismatch": (
            "---\nname: other\ndescription: X Load when.\n---\n\nBody.\n"
        ),
    }
    for folder_name, text in cases.items():
        folder = aldwin_home / "skills" / folder_name
        folder.mkdir()
        (folder / "SKILL.md").write_text(text, encoding="utf-8")
    # The name check applies only to packaged defaults, so a user skill named
    # "other" in the "name-mismatch" folder still loads.
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    by_folder = {e.path.name if e.path else e.name: e for e in cat}
    assert by_folder["no-fm"].skill is None
    assert by_folder["bad-yaml"].skill is None
    assert by_folder["not-map"].skill is None
    assert by_folder["empty-name"].skill is None
    assert by_folder["bad-tools"].skill is None


def test_oversized_skill_file_bytes(aldwin_home: Path, monkeypatch) -> None:
    from aldwin.core import skills as skills_mod

    monkeypatch.setattr(skills_mod, "MAX_USER_FILE_BYTES", 40)
    folder = aldwin_home / "skills" / "huge"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: huge\ndescription: Huge Load when testing please.\n---\n\n"
        "Body that exceeds the patched byte limit for this test case.\n",
        encoding="utf-8",
    )
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    row = next(e for e in cat if e.name == "huge")
    assert row.skill is None
    assert any("too large" in f for f in row.flags)


def test_migrate_skips_when_backup_exists(aldwin_home: Path) -> None:
    folder = aldwin_home / "skills" / STARTER_001_FOLDER
    folder.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "using-aldwin-001" / "SKILL.md"
    if not fixture.is_file():
        pytest.skip("0.0.1 starter fixture missing")
    (folder / "SKILL.md").write_bytes(fixture.read_bytes())
    backup = aldwin_home / "skills" / STARTER_001_BACKUP_FOLDER
    backup.mkdir()
    (backup / "SKILL.md").write_text("old\n", encoding="utf-8")
    assert migrate_001_starter_skill(aldwin_home) is False
    assert folder.exists()


def test_duplicate_folders_no_dirname_match(aldwin_home: Path) -> None:
    (aldwin_home / "skills").mkdir(exist_ok=True)
    _write_skill(
        aldwin_home / "skills" / "a-folder",
        "shared-name",
        "A Load when testing.",
    )
    _write_skill(
        aldwin_home / "skills" / "b-folder",
        "shared-name",
        "B Load when testing.",
    )
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    loaded = [e for e in cat if e.name == "shared-name" and e.skill]
    errors = [e for e in cat if e.skill is None and "duplicate" in " ".join(e.flags)]
    assert len(loaded) == 1
    assert errors


def test_to_catalog_items_maps_flags(aldwin_home: Path) -> None:
    from aldwin.core.skills import to_catalog_items

    (aldwin_home / "skills").mkdir(exist_ok=True)
    folder = aldwin_home / "skills" / "fancy"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: fancy\ndescription: Fancy Load when.\n"
        'tools: ["os:system"]\n---\n\nBody.\n',
        encoding="utf-8",
    )
    bad = aldwin_home / "skills" / "broken"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: broken\n---\n", encoding="utf-8")
    items = to_catalog_items(
        build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    )
    fancy = next(i for i in items if i["name"] == "fancy")
    fancy_flags = fancy["flags"]
    assert isinstance(fancy_flags, list)
    assert "tools removed" in fancy_flags
    broken = next(i for i in items if i["name"] == "broken")
    broken_flags = broken["flags"]
    assert isinstance(broken_flags, list)
    assert any(str(f).startswith("error:") for f in broken_flags)


def test_skills_fingerprint_tracks_user_files(aldwin_home: Path) -> None:
    (aldwin_home / "skills").mkdir(exist_ok=True)
    assert skills_fingerprint(aldwin_home) == ()
    _write_skill(
        aldwin_home / "skills" / "mine",
        "mine",
        "Mine Load when testing.",
    )
    fp = skills_fingerprint(aldwin_home)
    assert len(fp) == 1
    assert fp[0][0] == "mine/SKILL.md"
