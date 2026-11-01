"""Where other agent apps keep their MCP servers.

Each app is one YAML file in `core/assets/connectors/import_sources/`. The
files are data, not code: adding an app whose format Aldwin already reads is a
new file and a fixture, with no Python. A file that can't be loaded is skipped
with a warning, so one bad entry never hides the others.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

DEFAULT_MAX_BYTES: Final[int] = 1_048_576
WORKSPACE_TOKEN: Final[str] = "{workspace}"

Format = Literal["json", "jsonc", "toml", "yaml"]
Dialect = Literal["standard", "gemini", "opencode", "codex", "zed", "goose"]
Scope = Literal["user", "project"]
VerifiedFrom = Literal["source", "docs"]


class Location(BaseModel):
    """One place an app keeps a file, per platform."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: Scope
    paths: dict[Literal["darwin", "linux", "any"], StrictStr] = Field(min_length=1)
    also: list[StrictStr] = Field(default_factory=list)  # other spellings to try


class ImportSource(BaseModel):
    """One app Aldwin can import MCP servers from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    app: StrictStr = Field(min_length=1)
    homepage: StrictStr = Field(min_length=1)
    verified: date
    verified_from: VerifiedFrom
    format: Format
    dialect: Dialect
    servers_at: list[list[StrictStr]] = Field(min_length=1)
    max_bytes: StrictInt = Field(default=DEFAULT_MAX_BYTES, ge=1)
    locations: list[Location] = Field(min_length=1)


@dataclass(frozen=True)
class ResolvedLocation:
    scope: Scope
    path: Path


def sources_dir() -> Traversable:
    return files("aldwin.core.assets").joinpath("connectors/import_sources")


def load_import_sources(
    directory: Traversable | Path | None = None,
) -> tuple[list[ImportSource], list[str]]:
    """Every readable source, sorted by filename, plus one warning per bad file."""
    folder = sources_dir() if directory is None else directory
    sources: list[ImportSource] = []
    warnings: list[str] = []
    for entry in sorted(_yaml_files(folder), key=lambda e: e.name):
        try:
            data = yaml.safe_load(entry.read_text(encoding="utf-8"))
            sources.append(ImportSource.model_validate(data))
        except (yaml.YAMLError, ValidationError, OSError, UnicodeDecodeError) as exc:
            warnings.append(f"import source {entry.name}: {_reason(exc)}")
    return sources, warnings


def resolve_locations(
    source: ImportSource,
    *,
    user_home: Path,
    workspace: Path | None,
    platform: str = sys.platform,
) -> tuple[list[ResolvedLocation], list[str]]:
    """The paths to look at on this platform, `~` and `{workspace}` expanded.

    A project location is dropped with a warning when there is no workspace.
    """
    resolved: list[ResolvedLocation] = []
    warnings: list[str] = []
    for location in source.locations:
        by_platform: Mapping[str, str] = {str(k): v for k, v in location.paths.items()}
        spellings = [
            *([by_platform[platform]] if platform in by_platform else []),
            *([by_platform["any"]] if "any" in by_platform else []),
            *location.also,
        ]
        for spelling in spellings:
            if WORKSPACE_TOKEN in spelling and workspace is None:
                warnings.append(f"{source.app}: can't resolve {spelling}")
                continue
            expanded = spelling.replace(WORKSPACE_TOKEN, str(workspace or ""))
            path = Path(
                expanded.replace("~", str(user_home), 1)
                if expanded.startswith("~")
                else expanded
            )
            resolved.append(ResolvedLocation(location.scope, path))
    return resolved, warnings


def match_source(
    path: Path,
    sources: Iterable[ImportSource],
    *,
    user_home: Path,
    workspace: Path | None,
    platform: str = sys.platform,
) -> tuple[ImportSource, Scope] | None:
    """The source whose known location `path` is, if any."""
    target = _resolve(path)
    for source in sources:
        locations, _ = resolve_locations(
            source, user_home=user_home, workspace=workspace, platform=platform
        )
        for location in locations:
            if _resolve(location.path) == target:
                return source, location.scope
    return None


def _yaml_files(folder: Traversable | Path) -> Iterator[Traversable | Path]:
    if not folder.is_dir():
        return
    for entry in folder.iterdir():
        if entry.is_file() and entry.name.endswith((".yaml", ".yml")):
            yield entry


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        return f"{where}: {first['msg']}" if where else first["msg"]
    text = str(exc).split("\n", 1)[0]
    return text or type(exc).__name__
