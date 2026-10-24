"""`tools:` lockdown and `create_skill` path safety."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from agentling.tools import ToolCallError

from aldwin.core.skills import build_catalogue
from aldwin.core.tools.skill_tools import NO_TOOLS, validate_create_skill_args
from security._helpers import call_fail


def test_user_skill_tools_never_import_module(
    aldwin_home: Path,
) -> None:
    """User skill with `tools: [os:system]` loads as text; `import_module` unused."""
    folder = aldwin_home / "skills" / "fancy"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: fancy\ndescription: Fancy Load when testing.\n"
        'tools: ["os:system"]\n---\n\nBody.\n',
        encoding="utf-8",
    )
    real_import = importlib.import_module

    def guarded(name: str, package: str | None = None) -> object:
        if name == "os":
            raise AssertionError("import_module called for skill tools entry")
        if package is None:
            return real_import(name)
        return real_import(name, package=package)

    with patch("importlib.import_module", side_effect=guarded):
        cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    fancy = next(e for e in cat if e.name == "fancy")
    assert fancy.skill is not None
    assert fancy.source == "user"


async def test_create_skill_rejects_tools_and_dotdot(
    security_tools: SimpleNamespace,
    set_answer,
) -> None:
    """`create_skill` rejects `tools:` lines and `../` names."""
    with pytest.raises(ToolCallError, match=NO_TOOLS):
        validate_create_skill_args(
            "weekly-report",
            "Desc Load when.",
            "1. Do thing\ntools: []\n2. Done",
        )

    set_answer("yes")
    msg2 = await call_fail(
        security_tools.tools["create_skill"],
        {
            "name": "../escape",
            "description": "Escape Load when testing hooks.",
            "instructions": "Body only.\n",
        },
    )
    assert "name" in msg2.lower() or "invalid" in msg2.lower()
    skills = security_tools.home / "skills"
    assert not (skills / "escape").exists()
