"""Default and user skill catalogue: discovery, overrides, lockdown, migration."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path
from string import Template
from typing import Final, Literal

import yaml
from agentling import Skill
from agentling.errors import SkillLoadError

logger = logging.getLogger("aldwin.core.skills")

SKILLS_SUBDIR: Final[str] = "skills"
SKILL_FILENAME: Final[str] = "SKILL.md"
SKILL_TEMPLATE_FILENAME: Final[str] = "SKILL.md.tmpl"
MAX_USER_BODY_CHARS: Final[int] = 20_000
MAX_USER_FILE_BYTES: Final[int] = 64 * 1024
MAX_DESCRIPTION_CHARS: Final[int] = 200

Source = Literal["default", "user", "user override"]

DEFAULT_SKILL_NAMES: Final[tuple[str, ...]] = (
    "connectors",
    "documents",
    "files",
    "making-documents",
    "markdown-notes",
    "memory",
    "skill-creator",
    "terminal",
    "using-aldwin",
    "web-research",
)

# SHA-256 of the 0.0.1 shipped `using-aldwin/SKILL.md` (unchanged starter copy).
STARTER_001_HASH: Final[str] = (
    "f3dddd6317d417653ffb560fd449dc802f8be2514c679b0c62cce6b96f34c1c1"
)
STARTER_001_FOLDER: Final[str] = "using-aldwin"
STARTER_001_BACKUP_FOLDER: Final[str] = "using-aldwin.old-0.0.1"

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class LoadedSkill:
    """One catalogue entry: effective skill text plus source and flags."""

    name: str
    description: str
    source: Source
    flags: tuple[str, ...]
    body: str
    path: Path | None
    skill: Skill | None


def _assets_skills_root() -> Path:
    """Return the filesystem path to packaged `core/assets/skills`."""
    root = files("aldwin.core.assets").joinpath("skills")
    return Path(str(root))


def _split_frontmatter(source: str) -> tuple[dict[str, object], str]:
    """Parse YAML frontmatter and body from a SKILL.md string."""
    match = _FRONTMATTER_RE.match(source)
    if match is None:
        raise ValueError("missing YAML frontmatter")
    raw_meta, body = match.group(1), match.group(2)
    try:
        meta = yaml.safe_load(raw_meta)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a mapping")
    return meta, body.strip()


def _clean_reason(exc: BaseException) -> str:
    """Format an exception into a single-line reason for `/skills`."""
    first_line = (
        str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    )
    return " ".join(first_line.split())


def _skill_from_text(
    *,
    text: str,
    folder: Path,
    expect_name: str | None = None,
) -> Skill:
    """Build a `Skill` from SKILL.md text without calling `load_tools`."""
    meta, body = _split_frontmatter(text)
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("frontmatter 'name' must be a non-empty string.")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("frontmatter 'description' must be a non-empty string.")
    cleaned_name = name.strip()
    if "\n" in cleaned_name or "\r" in cleaned_name:
        raise ValueError("frontmatter 'name' must be a single line.")
    cleaned_desc = " ".join(description.split())
    if expect_name is not None and cleaned_name != expect_name:
        raise ValueError(
            f"frontmatter name {cleaned_name!r} does not match folder {expect_name!r}."
        )
    raw_tools = meta.get("tools", [])
    if raw_tools is None:
        raw_tools = []
    if not isinstance(raw_tools, list) or not all(
        isinstance(item, str) for item in raw_tools
    ):
        raise ValueError("'tools' must be a list of strings.")
    return Skill(
        name=cleaned_name,
        description=cleaned_desc,
        instructions=body,
        path=folder,
        tools=list(raw_tools),
    )


def _lockdown(skill: Skill, *, user: bool) -> tuple[Skill, tuple[str, ...]]:
    """Strip `tools:` so skills never import code. Return flags if stripped."""
    if not skill.tools:
        return skill, ()
    if user:
        logger.warning(
            'skill "%s": tools removed (skills can\'t load code)',
            skill.name,
        )
    else:
        logger.warning(
            'default skill "%s": tools removed (skills can\'t load code)',
            skill.name,
        )
    return replace(skill, tools=[]), ("tools removed",)


def _load_default_skill(
    name: str,
    *,
    version: str,
    model: str,
) -> LoadedSkill:
    """Load one packaged default skill (rendered when it ships a template)."""
    folder = _assets_skills_root() / name
    text = _default_skill_source(folder, version=version, model=model)
    skill = _skill_from_text(text=text, folder=folder, expect_name=name)
    skill, flags = _lockdown(skill, user=False)
    return LoadedSkill(
        name=skill.name,
        description=skill.description,
        source="default",
        flags=flags,
        body=skill.instructions,
        path=None,
        skill=skill,
    )


def _error_entry(
    *,
    name: str,
    reason: str,
    path: Path | None,
    source: Source = "user",
) -> LoadedSkill:
    """Catalogue row for a skill that failed to load."""
    return LoadedSkill(
        name=name,
        description="",
        source=source,
        flags=(f"error: {reason}",),
        body="",
        path=path,
        skill=None,
    )


def _load_user_folder(folder: Path) -> LoadedSkill:
    """Load one `~/.aldwin/skills/<folder>/SKILL.md`, or an error entry."""
    skill_file = folder / SKILL_FILENAME
    if not skill_file.is_file():
        return _error_entry(
            name=folder.name,
            reason=f"missing {SKILL_FILENAME}",
            path=folder,
        )
    try:
        size = skill_file.stat().st_size
        if size > MAX_USER_FILE_BYTES:
            raise ValueError(
                f"skill file too large ({size} bytes; limit {MAX_USER_FILE_BYTES})"
            )
        text = skill_file.read_text(encoding="utf-8")
        skill = _skill_from_text(text=text, folder=folder)
        if len(skill.instructions) > MAX_USER_BODY_CHARS:
            raise ValueError(f"body too long (limit {MAX_USER_BODY_CHARS} characters)")
        if len(skill.description) > MAX_DESCRIPTION_CHARS:
            raise ValueError(
                f"description too long (limit {MAX_DESCRIPTION_CHARS} characters)"
            )
        skill, flags = _lockdown(skill, user=True)
        return LoadedSkill(
            name=skill.name,
            description=skill.description,
            source="user",
            flags=flags,
            body=skill.instructions,
            path=folder,
            skill=skill,
        )
    except (
        OSError,
        yaml.YAMLError,
        ValueError,
        TypeError,
        SkillLoadError,
        UnicodeError,
    ) as exc:
        reason = _clean_reason(exc)
        logger.warning("Skipping invalid skill in %s: %s", folder, reason)
        return _error_entry(name=folder.name, reason=reason, path=folder)


def migrate_001_starter_skill(home: Path) -> bool:
    """Rename an unedited 0.0.1 `using-aldwin` copy so the new default applies.

    Returns True when a rename happened.
    """
    root = home.expanduser().resolve()
    folder = root / SKILLS_SUBDIR / STARTER_001_FOLDER
    skill_file = folder / SKILL_FILENAME
    if not skill_file.is_file():
        return False
    backup = root / SKILLS_SUBDIR / STARTER_001_BACKUP_FOLDER
    if backup.exists():
        return False
    try:
        digest = hashlib.sha256(skill_file.read_bytes()).hexdigest()
    except OSError:
        return False
    if digest != STARTER_001_HASH:
        return False
    try:
        folder.rename(backup)
    except OSError as exc:
        logger.warning("Failed to rename 0.0.1 starter skill: %s", exc)
        return False
    logger.info(
        "Renamed unedited 0.0.1 starter skill to %s",
        STARTER_001_BACKUP_FOLDER,
    )
    return True


def build_catalogue(
    home: Path,
    *,
    version: str,
    model: str,
) -> list[LoadedSkill]:
    """Load defaults and user skills with override precedence and lockdown."""
    migrate_001_starter_skill(home)

    by_name: dict[str, LoadedSkill] = {}
    for name in DEFAULT_SKILL_NAMES:
        try:
            by_name[name] = _load_default_skill(name, version=version, model=model)
        except (OSError, ValueError, TypeError, SkillLoadError) as exc:
            logger.error("Failed to load default skill %s: %s", name, exc)
            by_name[name] = _error_entry(
                name=name,
                reason=_clean_reason(exc),
                path=None,
                source="default",
            )

    skills_dir = home.expanduser().resolve() / SKILLS_SUBDIR
    errors: list[LoadedSkill] = []
    user_by_name: dict[str, list[LoadedSkill]] = {}

    if skills_dir.is_dir():
        candidates = sorted(
            (
                p
                for p in skills_dir.iterdir()
                if p.is_dir()
                and not p.name.startswith(".")
                and p.name != STARTER_001_BACKUP_FOLDER
            ),
            key=lambda p: p.name,
        )
        for folder in candidates:
            loaded = _load_user_folder(folder)
            if loaded.skill is None:
                errors.append(loaded)
                continue
            user_by_name.setdefault(loaded.name, []).append(loaded)

    for name, candidates_list in user_by_name.items():
        if len(candidates_list) == 1:
            chosen = candidates_list[0]
        else:
            matching = [c for c in candidates_list if c.path and c.path.name == name]
            if matching:
                chosen = matching[0]
                for other in candidates_list:
                    if other is not chosen:
                        logger.warning(
                            "Skipping duplicate skill %r in %s "
                            "(folder name does not match frontmatter name)",
                            name,
                            other.path,
                        )
                        errors.append(
                            _error_entry(
                                name=other.path.name if other.path else name,
                                reason=f"duplicate name {name!r}",
                                path=other.path,
                            )
                        )
            else:
                chosen = candidates_list[0]
                for other in candidates_list[1:]:
                    logger.warning(
                        "Skipping duplicate skill %r in %s",
                        name,
                        other.path,
                    )
                    errors.append(
                        _error_entry(
                            name=other.path.name if other.path else name,
                            reason=f"duplicate name {name!r}",
                            path=other.path,
                        )
                    )

        if name in by_name and by_name[name].source == "default":
            chosen = LoadedSkill(
                name=chosen.name,
                description=chosen.description,
                source="user override",
                flags=chosen.flags,
                body=chosen.body,
                path=chosen.path,
                skill=chosen.skill,
            )
        by_name[name] = chosen

    result = list(by_name.values()) + errors
    result.sort(key=lambda s: s.name)
    return result


def catalogue_skills_for_agent(catalogue: list[LoadedSkill]) -> list[Skill]:
    """Return Agentling `Skill` objects from a catalogue (skipping errors)."""
    return [entry.skill for entry in catalogue if entry.skill is not None]


def default_skill_text(
    name: str, *, version: str = "0.0.2", model: str = "gpt-5"
) -> str:
    """Return the packaged SKILL.md text for `name` (rendered if templated)."""
    return _default_skill_source(
        _assets_skills_root() / name, version=version, model=model
    )


def _default_skill_source(folder: Path, *, version: str, model: str) -> str:
    """A default skill's text: `SKILL.md`, or its template rendered with the
    version, the model and the connector catalogue.
    """
    template = folder / SKILL_TEMPLATE_FILENAME
    if not template.is_file():
        return (folder / SKILL_FILENAME).read_text(encoding="utf-8")
    raw = template.read_text(encoding="utf-8")
    return Template(raw).safe_substitute(
        version=version, model=model, catalogue=_catalogue_lines()
    )


def _catalogue_lines() -> str:
    from aldwin.core.connectors.catalog import load_catalog

    try:
        entries = load_catalog()
    except Exception:  # a broken catalogue must not stop skills loading
        logger.exception("connector catalogue could not be loaded for skills")
        return "(catalogue unavailable; use /connectors)"
    return "\n".join(f"- {e.name} ({e.title}): {e.purpose}" for e in entries)


def skills_fingerprint(home: Path) -> tuple[tuple[str, int, int], ...]:
    """Return sorted `(rel_path, mtime_ns, size)` for every user `SKILL.md`."""
    skills_dir = home.expanduser().resolve() / SKILLS_SUBDIR
    if not skills_dir.is_dir():
        return ()
    entries: list[tuple[str, int, int]] = []
    for folder in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        if folder.name == STARTER_001_BACKUP_FOLDER:
            continue
        skill_file = folder / SKILL_FILENAME
        if not skill_file.is_file():
            continue
        try:
            st = skill_file.stat()
        except OSError:
            continue
        rel = f"{folder.name}/{SKILL_FILENAME}"
        entries.append((rel, int(st.st_mtime_ns), int(st.st_size)))
    return tuple(entries)


def to_catalog_items(
    catalogue: list[LoadedSkill],
    *,
    last_used: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """Map catalogue entries to protocol `SkillCatalogItem` field dicts."""
    recent = last_used or {}
    items: list[dict[str, object]] = []
    for entry in catalogue:
        items.append(
            {
                "name": entry.name,
                "description": entry.description,
                "source": entry.source,
                "flags": list(entry.flags),
                "last_used": recent.get(entry.name),
            }
        )
    return items


PICKED_SKILLS_CAP: Final[int] = 10_000
MAX_PICKED_SKILLS: Final[int] = 3
_SKILL_CUT_MARKER: Final[str] = "…(skill cut to fit)"

# Longer closing tags first so partial replacements stay consistent.
_MARKUP_ESCAPES: Final[tuple[tuple[str, str], ...]] = (
    ("</picked_skills>", "&lt;/picked_skills>"),
    ("<picked_skills>", "&lt;picked_skills>"),
    ("</user_message>", "&lt;/user_message>"),
    ("<user_message>", "&lt;user_message>"),
    ("</skill>", "&lt;/skill>"),
    ("<skill", "&lt;skill"),
)


def escape_skill_markup(text: str) -> str:
    """Escape tags that could fake the picked-skills task structure."""
    out = text
    for old, new in _MARKUP_ESCAPES:
        out = out.replace(old, new)
    return out


def _is_error_skill(entry: LoadedSkill) -> bool:
    return any(flag.startswith("error:") for flag in entry.flags)


def resolve_picked_skills(
    names: list[str],
    catalogue: list[LoadedSkill] | dict[str, LoadedSkill],
    *,
    limit: int = MAX_PICKED_SKILLS,
) -> tuple[list[LoadedSkill], list[tuple[str, Literal["unknown", "limit"]]]]:
    """Validate `@` picks: dedupe, skip errors/unknown, cap at `limit`.

    Returns `(picked, dropped)` where each dropped entry is `(name, reason)`.
    """
    by_name: dict[str, LoadedSkill]
    if isinstance(catalogue, dict):
        by_name = catalogue
    else:
        by_name = {entry.name: entry for entry in catalogue}

    picked: list[LoadedSkill] = []
    dropped: list[tuple[str, Literal["unknown", "limit"]]] = []
    seen: set[str] = set()

    for name in names:
        cleaned = name.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        entry = by_name.get(cleaned)
        if entry is None or entry.skill is None or _is_error_skill(entry):
            dropped.append((cleaned, "unknown"))
            continue
        if len(picked) >= limit:
            dropped.append((cleaned, "limit"))
            continue
        picked.append(entry)
    return picked, dropped


def build_task(user_text: str, picked: list[LoadedSkill]) -> str:
    """Wrap `@`-picked skill bodies ahead of the user message for Agentling.

    With no picks, returns `user_text` unchanged. Escapes markup in bodies and
    user text; caps total picked section at `PICKED_SKILLS_CAP` characters.
    """
    if not picked:
        return user_text

    skill_blocks: list[str] = []
    remaining = PICKED_SKILLS_CAP
    header = (
        "<picked_skills>\nThe user picked these skills for this message. Follow them.\n"
    )
    footer = "</picked_skills>"
    # Reserve space for wrapper lines around each body.
    remaining -= len(header) + len(footer)

    for entry in picked:
        open_tag = f'<skill name="{entry.name}">\n'
        close_tag = "\n</skill>\n"
        overhead = len(open_tag) + len(close_tag)
        if remaining <= overhead + len(_SKILL_CUT_MARKER):
            logger.warning(
                "Picked skill %r omitted: picked text already at %s char cap",
                entry.name,
                PICKED_SKILLS_CAP,
            )
            break
        body = escape_skill_markup(entry.body)
        budget = remaining - overhead
        if len(body) > budget:
            keep = max(0, budget - len(_SKILL_CUT_MARKER))
            body = body[:keep] + _SKILL_CUT_MARKER
            logger.warning(
                "Picked skill %r cut to fit %s char cap",
                entry.name,
                PICKED_SKILLS_CAP,
            )
        block = f"{open_tag}{body}{close_tag}"
        skill_blocks.append(block)
        remaining -= len(block)

    if not skill_blocks:
        return user_text

    escaped_user = escape_skill_markup(user_text)
    return (
        f"{header}{''.join(skill_blocks)}{footer}\n\n"
        f"<user_message>\n{escaped_user}\n</user_message>"
    )
