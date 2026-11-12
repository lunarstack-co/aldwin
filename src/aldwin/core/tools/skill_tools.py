"""`create_skill` tool: write or replace a user skill after Diff approval."""

from __future__ import annotations

import difflib
import os
import re
import tempfile
import time
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, Final

import yaml
from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.home import DIR_MODE, FILE_MODE, home_dir
from aldwin.core.policy import ApprovalPreview, looks_like_secret
from aldwin.core.skills import (
    DEFAULT_SKILL_NAMES,
    SKILL_FILENAME,
    SKILLS_SUBDIR,
    default_skill_text,
)
from aldwin.core.tools import ToolOutput
from aldwin.core.tools import display as core_display

NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
TOOLS_LINE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*tools\s*:", re.MULTILINE)
MAX_NAME_LEN: Final[int] = 64
MAX_DESCRIPTION_LEN: Final[int] = 200
MAX_INSTRUCTIONS_LEN: Final[int] = 20_000

INVALID_NAME: Final[str] = (
    "invalid skill name: use lowercase letters, digits and single hyphens, "
    "up to 64 characters"
)
INVALID_DESCRIPTION: Final[str] = "description must be one line, 1-200 characters"
INVALID_INSTRUCTIONS: Final[str] = "instructions must be 1-20000 characters"
BODY_ONLY: Final[str] = (
    "instructions must be the body only; name and description are separate arguments"
)
NO_TOOLS: Final[str] = "skills can't declare tools"
NO_SECRETS: Final[str] = "skills can't contain keys or passwords"
PATH_REFUSED: Final[str] = "refused: skills can only be written under ~/.aldwin/skills"


def _home(home: Path | None) -> Path:
    return (home if home is not None else home_dir()).expanduser().resolve()


def validate_create_skill_args(
    name: str,
    description: str,
    instructions: str,
) -> tuple[str, str, str]:
    """Validate args; raise `ToolCallError` with the exact refusal text."""
    if (
        not isinstance(name, str)
        or not NAME_RE.fullmatch(name)
        or len(name) > MAX_NAME_LEN
    ):
        raise ToolCallError(INVALID_NAME)

    if not isinstance(description, str):
        raise ToolCallError(INVALID_DESCRIPTION)
    stripped_desc = description.strip()
    if (
        not stripped_desc
        or "\n" in description
        or "\r" in description
        or len(stripped_desc) > MAX_DESCRIPTION_LEN
    ):
        raise ToolCallError(INVALID_DESCRIPTION)

    if not isinstance(instructions, str):
        raise ToolCallError(INVALID_INSTRUCTIONS)
    if not instructions.strip() or len(instructions) > MAX_INSTRUCTIONS_LEN:
        raise ToolCallError(INVALID_INSTRUCTIONS)
    if instructions.lstrip().startswith("---"):
        raise ToolCallError(BODY_ONLY)
    head = "\n".join(instructions.splitlines()[:10])
    if TOOLS_LINE_RE.search(head):
        raise ToolCallError(NO_TOOLS)
    if looks_like_secret(stripped_desc) or looks_like_secret(instructions):
        raise ToolCallError(NO_SECRETS)

    return name, stripped_desc, instructions


def build_skill_file_text(name: str, description: str, instructions: str) -> str:
    """Return SKILL.md text: YAML name/description frontmatter, then body only."""
    front = yaml.safe_dump(
        {"name": name, "description": description},
        sort_keys=False,
        allow_unicode=True,
    )
    body = instructions if instructions.endswith("\n") else instructions + "\n"
    return f"---\n{front}---\n\n{body}"


def _target_path(home: Path, name: str) -> Path:
    skills_root = (home / SKILLS_SUBDIR).resolve()
    target = (skills_root / name / SKILL_FILENAME).resolve()
    try:
        target.relative_to(skills_root)
    except ValueError as exc:
        raise ToolCallError(PATH_REFUSED) from exc
    if name not in target.parts:
        raise ToolCallError(PATH_REFUSED)
    return target


def _unified_diff(old: str, new: str, *, fromfile: str, tofile: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
            n=3,
        )
    )


def _count_diff_stats(diff: str) -> tuple[int, int]:
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


class CreateSkillTool(Tool):
    """Write `~/.aldwin/skills/<name>/SKILL.md` after Diff approval."""

    name = "create_skill"
    description = (
        "Create or update a user skill under ~/.aldwin/skills/<name>/SKILL.md. "
        "Pass name, description (one line, when to load), and instructions "
        "(body only). Always shows a diff for approval; never grantable for "
        "the session."
    )
    parallel_safe = False
    timeout = None
    max_output_chars = 500
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Lowercase hyphenated skill name (up to 64 characters).",
            },
            "description": {
                "type": "string",
                "description": (
                    "One-line description that says what the skill does and when "
                    "to load it (1-200 characters)."
                ),
            },
            "instructions": {
                "type": "string",
                "description": (
                    "Markdown body only (no frontmatter). Numbered steps "
                    "and one example."
                ),
            },
        },
        "required": ["name", "description", "instructions"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        home: Path | None = None,
        host_flags: MutableMapping[str, bool] | None = None,
        version: str = "0.0.2",
        model: str = "gpt-5",
    ) -> None:
        self._home = home
        self._host_flags = host_flags if host_flags is not None else {}
        self._version = version
        self._model = model

    def _prepare_state(
        self,
        name: str,
        description: str,
        instructions: str,
    ) -> tuple[Path, str, str, str, int | None, str, str]:
        """Return target, file text, summary, detail, mtime_ns, kind."""
        name, description, instructions = validate_create_skill_args(
            name, description, instructions
        )
        root = _home(self._home)
        target = _target_path(root, name)
        new_text = build_skill_file_text(name, description, instructions)
        line_count = len(new_text.splitlines())

        existing_user = target.is_file()
        mtime_ns: int | None = None
        if existing_user:
            mtime_ns = target.stat().st_mtime_ns
            old_text = target.read_text(encoding="utf-8")
            diff_str = _unified_diff(
                old_text,
                new_text,
                fromfile=f"a/{name}/{SKILL_FILENAME}",
                tofile=f"b/{name}/{SKILL_FILENAME}",
            )
            detail = diff_str
            added, removed = _count_diff_stats(detail)
            summary = f"Update skill {name} (+{added} −{removed})"
            kind = "updated"
        elif name in DEFAULT_SKILL_NAMES:
            packaged = default_skill_text(
                name, version=self._version, model=self._model
            )
            diff_str = _unified_diff(
                packaged,
                new_text,
                fromfile=f"a/default/{name}/{SKILL_FILENAME}",
                tofile=f"b/{name}/{SKILL_FILENAME}",
            )
            added, removed = _count_diff_stats(diff_str)
            summary = f"Replace default skill {name} (+{added} −{removed})"
            detail = (
                f'This replaces the default "{name}" skill. '
                f"Delete ~/.aldwin/skills/{name}/ to restore it.\n"
                f"{diff_str}"
            )
            kind = "replaces default"
        else:
            summary = f"New skill {name} ({line_count} lines)"
            detail = new_text
            diff_str = _unified_diff(
                "",
                new_text,
                fromfile="/dev/null",
                tofile=f"b/{name}/{SKILL_FILENAME}",
            )
            kind = "new"

        return target, new_text, summary, detail, mtime_ns, kind, diff_str

    def prepare_approval(
        self,
        name: str,
        description: str,
        instructions: str,
    ) -> ApprovalPreview:
        """Validate and build Diff preview; never writes."""
        _target, _text, summary, detail, mtime_ns, _kind, diff_str = (
            self._prepare_state(name, description, instructions)
        )
        clean_name = name.strip()
        return ApprovalPreview(
            summary=summary,
            detail=detail,
            mtime_ns=mtime_ns,
            display=core_display.diff(
                path=f"skills/{clean_name}/{SKILL_FILENAME}",
                unified=diff_str,
            ),
        )

    async def forward(  # type: ignore[override]
        self,
        name: str,
        description: str,
        instructions: str,
    ) -> ToolOutput:
        started = time.monotonic()
        target, text, _summary, _detail, _mtime, kind, _diff_str = self._prepare_state(
            name, description, instructions
        )
        try:
            await _atomic_write_skill(target, text)
        except OSError as exc:
            elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
            return ToolOutput(
                f"failed: {exc}",
                summary=f"Skill failed · {name}",
                ok=False,
                duration_ms=elapsed_ms,
            )

        self._host_flags["skills_dirty"] = True
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        if kind == "new":
            result = f"saved skill {name} (new)"
            row = f"Skill created · {name} · type @{name} to use it"
        elif kind == "updated":
            result = f"saved skill {name} (updated)"
            row = f"Skill updated · {name} · type @{name} to use it"
        else:
            result = f"saved skill {name} (replaces default)"
            row = f"Skill updated · {name} · type @{name} to use it"
        return ToolOutput(
            result,
            summary=row,
            ok=True,
            duration_ms=elapsed_ms,
        )


async def _atomic_write_skill(target: Path, text: str) -> None:
    """Write `text` to `target` with 0700/0600 and atomic replace."""
    import asyncio

    def _write() -> None:
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        os.chmod(parent, DIR_MODE)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=parent,
                prefix=f".{target.stem}.",
                suffix=".aldwin-tmp",
                delete=False,
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                tmp_file.write(text.encode("utf-8"))
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.chmod(tmp_path, FILE_MODE)
            os.replace(tmp_path, target)
            os.chmod(target, FILE_MODE)
        except BaseException:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise

    await asyncio.to_thread(_write)


def build_create_skill_tool(
    *,
    home: Path | None = None,
    host_flags: MutableMapping[str, bool] | None = None,
    version: str = "0.0.2",
    model: str = "gpt-5",
) -> CreateSkillTool:
    """Construct `CreateSkillTool` with injected home and dirty flags."""
    return CreateSkillTool(
        home=home,
        host_flags=host_flags,
        version=version,
        model=model,
    )
