"""Architecture boundary guard tests (`FR-8`, `TC-10`)."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

import yaml

from aldwin.core.settings import SecretsConfig
from aldwin.core.tools import BUILTIN_TOOL_NAMES, builtin_tools

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ALDWIN = REPO_ROOT / "src" / "aldwin"


async def _dummy_ask(_summary: str, _detail: str = "") -> bool:
    return True


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            for alias in node.names:
                modules.add(f"{node.module}.{alias.name}")
    return modules


def check_forbidden_imports(
    directory: Path,
    forbidden: Sequence[str],
) -> list[str]:
    """Return violation strings for any forbidden import under `directory`."""
    py_files = sorted(directory.rglob("*.py"))
    assert py_files, f"Expected at least one Python file in {directory}"

    violations: list[str] = []
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        imported = _imported_modules(tree)

        for target in forbidden:
            ast_hit = any(
                mod == target or mod.startswith(f"{target}.") for mod in imported
            )
            text_hit = target in text
            if ast_hit or text_hit:
                violations.append(f"{path}: forbidden import {target!r}")
    return violations


def test_cli_never_imports_core() -> None:
    """A door (`aldwin.cli`) must never import `aldwin.core` or Agentling."""
    assert (
        check_forbidden_imports(
            SRC_ALDWIN / "cli",
            ("aldwin.core", "agentling"),
        )
        == []
    )


def test_client_never_imports_core_or_agentling() -> None:
    """The client (`aldwin.client`) must never import `aldwin.core` or Agentling."""
    assert (
        check_forbidden_imports(
            SRC_ALDWIN / "client",
            ("aldwin.core", "agentling"),
        )
        == []
    )


def test_protocol_never_imports_core_or_agentling() -> None:
    """The protocol (`aldwin.protocol`) only depends on stdlib and Pydantic."""
    assert (
        check_forbidden_imports(
            SRC_ALDWIN / "protocol",
            ("aldwin.core", "agentling"),
        )
        == []
    )


def test_core_never_imports_protocol_host_client_or_cli() -> None:
    """The brain (`aldwin.core`) must never import protocol, host, client, or cli."""
    assert (
        check_forbidden_imports(
            SRC_ALDWIN / "core",
            ("aldwin.protocol", "aldwin.host", "aldwin.client", "aldwin.cli"),
        )
        == []
    )


def test_builtin_tools_returns_exact_six_names(aldwin_home: Path) -> None:
    """`FR-8`: `builtin_tools()` returns the exact six expected tool names."""
    tools = builtin_tools(_dummy_ask, home=aldwin_home, secrets=SecretsConfig())
    names = tuple(t.name for t in tools)
    assert names == (
        "web_search",
        "web_fetch",
        "run_command",
        "remember",
        "recall",
        "forget",
        "set_persona",
    )
    assert names == BUILTIN_TOOL_NAMES


def test_architecture_guard_catches_deliberate_bad_import(tmp_path: Path) -> None:
    """`TC-10`: `check_forbidden_imports` flags a deliberate bad import."""
    bad_dir = tmp_path / "fake_cli"
    bad_dir.mkdir()
    bad_file = bad_dir / "bad_door.py"
    bad_file.write_text(
        "from agentling import Agent\nfrom aldwin import core\n",
        encoding="utf-8",
    )

    violations = check_forbidden_imports(bad_dir, ("aldwin.core", "agentling"))
    assert len(violations) == 2
    assert any("aldwin.core" in v for v in violations)
    assert any("agentling" in v for v in violations)


def test_ci_workflow_runs_matrix_and_uploads_coverage() -> None:
    """ENG-433 FR-20: CI matrix 3.11–3.13, security job, coverage upload."""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.is_file()
    data = yaml.safe_load(ci_path.read_text(encoding="utf-8"))

    job = data["jobs"]["test"]
    matrix = job["strategy"]["matrix"]
    assert matrix["os"] == ["ubuntu-latest", "macos-latest"]
    assert matrix["python-version"] == ["3.11", "3.12", "3.13"]

    steps = job["steps"]
    runs = [step.get("run", "") for step in steps]
    assert any("ruff check" in r for r in runs)
    assert any("ruff format --check" in r for r in runs)
    assert any("mypy" in r for r in runs)
    assert any(
        "--cov=aldwin" in r
        and "--cov-report=xml:coverage.xml" in r
        and "--cov-fail-under=90" in r
        for r in runs
    )
    assert any("pytest -m perf" in r for r in runs)
    assert any(
        step.get("uses") == "actions/upload-artifact@v4"
        and step.get("with", {}).get("path") == "coverage.xml"
        for step in steps
    )

    security = data["jobs"]["security"]
    sec_runs = [step.get("run", "") for step in security["steps"]]
    assert any("tests/security" in r for r in sec_runs)

    e2e_path = REPO_ROOT / ".github" / "workflows" / "e2e.yml"
    assert e2e_path.is_file()
    e2e_text = e2e_path.read_text(encoding="utf-8")
    assert "workflow_dispatch" in e2e_text
    assert "schedule:" in e2e_text
    assert "pytest -m e2e" in e2e_text


def test_manual_test_doc_exists_and_covers_all_fr11_steps() -> None:
    """`FR-11`, `FR-12` & `TC-12`: `docs/manual-test.md` covers all required steps."""
    doc_path = REPO_ROOT / "docs" / "manual-test.md"
    assert doc_path.is_file()
    text = doc_path.read_text(encoding="utf-8")

    required_fragments = (
        "uv tool install",
        "That key was rejected by OpenAI",
        "web_search",
        "Run this command?",
        "(stopped)",
        "Your last request was interrupted",
        "continue",
        "Update persona?",
        "/model",
        "/memory",
        "/skills",
        "/tools",
        "/new",
        "/help",
        "SKILL.md",
        "Ctrl-C",
        "Ctrl-D",
        "NO_COLOR=1",
        "--plain",
        "uv tool upgrade",
        "aldwin uninstall",
        "--purge",
        "Manual test run log template",
    )
    for fragment in required_fragments:
        assert fragment in text, f"Missing {fragment!r} in docs/manual-test.md"
