"""The bundled import sources: every file loads, has a fixture, and is documented."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aldwin.core.connectors.import_dialects import to_spec
from aldwin.core.connectors.import_sources import (
    ImportSource,
    load_import_sources,
    resolve_locations,
    sources_dir,
)
from aldwin.core.connectors.importer import _load_known

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "import"
DOCS = REPO / "docs" / "import-sources.md"
EXPECTED_APPS = [
    "Claude Code",
    "Claude Desktop",
    "Cline",
    "Codex CLI",
    "GitHub Copilot CLI",
    "Cursor",
    "Devin",
    "Gemini CLI",
    "Goose",
    "Hermes Agent",
    "nanobot",
    "opencode",
    "Pi",
    "VS Code",
    "Zed",
]


def _sources() -> list[ImportSource]:
    sources, warnings = load_import_sources()
    assert warnings == []
    return sources


def _fixture_for(source_file: str) -> Path:
    stem = Path(source_file).stem.replace("-", "_")
    matches = list(FIXTURES.glob(f"{stem}.*"))
    assert len(matches) == 1, f"expected one fixture for {source_file}, got {matches}"
    return matches[0]


def test_bundled_sources_load_in_filename_order() -> None:
    sources = _sources()
    assert [s.app for s in sources] == EXPECTED_APPS
    for source in sources:
        assert source.homepage.startswith("https://")
        assert source.verified.year >= 2026
        for location in source.locations:
            assert set(location.paths) & {"darwin", "linux", "any"}
            if "any" not in location.paths:
                assert {"darwin", "linux"} <= set(location.paths)
            for spelling in (*location.paths.values(), *location.also):
                if location.scope == "project":
                    assert "{workspace}" in spelling
                else:
                    assert spelling.startswith("~")


def test_bad_source_files_warn_and_the_rest_load(tmp_path: Path) -> None:
    folder = tmp_path / "sources"
    folder.mkdir()
    for entry in sources_dir().iterdir():
        (folder / entry.name).write_text(entry.read_text(encoding="utf-8"), "utf-8")
    (folder / "broken.yaml").write_text("app: [unclosed", "utf-8")
    (folder / "odd.yaml").write_text(
        (folder / "cursor.yaml")
        .read_text("utf-8")
        .replace("dialect: standard", "dialect: weird"),
        "utf-8",
    )
    sources, warnings = load_import_sources(folder)
    assert len(sources) == len(EXPECTED_APPS)
    assert len(warnings) == 2
    assert warnings[0].startswith("import source broken.yaml: ")
    assert warnings[1].startswith("import source odd.yaml: dialect: ")


@pytest.mark.parametrize("source_file", sorted(p.name for p in sources_dir().iterdir()))
def test_every_source_has_a_fixture_that_imports(
    source_file: str, tmp_path: Path
) -> None:
    import yaml

    source_entry = next(e for e in sources_dir().iterdir() if e.name == source_file)
    source = ImportSource.model_validate(
        yaml.safe_load(source_entry.read_text(encoding="utf-8"))
    )
    fixture = _fixture_for(source_file)
    loaded = _load_known(fixture, source, "user", tmp_path)
    assert loaded.warnings == ()
    specs = []
    for _, entry in loaded.servers:
        try:
            specs.append(to_spec(source.dialect, entry))
        except Exception:  # noqa: BLE001  the fixture also holds deliberate failures
            continue
    assert specs, f"{fixture.name} imports nothing"


def test_every_source_is_documented_and_every_row_has_a_source() -> None:
    text = DOCS.read_text("utf-8")
    table = text.split("## Supported apps", 1)[1].split("###", 1)[0]
    rows = [
        line.split("|")[1].strip()
        for line in table.splitlines()
        if line.startswith("| ")
        and not line.startswith("| App")
        and not line.startswith("|---")
    ]
    apps = [s.app for s in _sources()]
    assert sorted(rows) == sorted(apps)
    for app in apps:
        assert re.search(rf"^\| {re.escape(app)} \|", text, re.MULTILINE)


def test_locations_expand_home_and_workspace(tmp_path: Path) -> None:
    source = next(s for s in _sources() if s.app == "Cursor")
    locations, warnings = resolve_locations(
        source, user_home=tmp_path / "me", workspace=tmp_path / "ws", platform="linux"
    )
    assert warnings == []
    assert [(loc.scope, loc.path) for loc in locations] == [
        ("user", tmp_path / "me" / ".cursor" / "mcp.json"),
        ("project", tmp_path / "ws" / ".cursor" / "mcp.json"),
    ]
    without, warnings = resolve_locations(
        source, user_home=tmp_path / "me", workspace=None, platform="linux"
    )
    assert [loc.scope for loc in without] == ["user"]
    assert warnings == ["Cursor: can't resolve {workspace}/.cursor/mcp.json"]


def test_platform_specific_paths(tmp_path: Path) -> None:
    desktop = next(s for s in _sources() if s.app == "Claude Desktop")
    mac, _ = resolve_locations(
        desktop, user_home=tmp_path, workspace=None, platform="darwin"
    )
    linux, _ = resolve_locations(
        desktop, user_home=tmp_path, workspace=None, platform="linux"
    )
    assert (
        mac[0].path
        == tmp_path / "Library/Application Support/Claude/claude_desktop_config.json"
    )
    assert linux[0].path == tmp_path / ".config/Claude/claude_desktop_config.json"
