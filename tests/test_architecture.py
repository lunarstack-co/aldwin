"""Architecture boundary guard tests (`FR-8`, `TC-10`)."""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Sequence
from pathlib import Path

import yaml
from packaging.version import Version

from aldwin.core.settings import SecretsConfig
from aldwin.core.tools import BUILTIN_TOOL_NAMES, builtin_tools

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ALDWIN = REPO_ROOT / "src" / "aldwin"
MCP_MODULES = ("mcp", "mcp_types", "httpx2")


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


def mcp_import_violations(root: Path) -> list[str]:
    """Return files under `root` that import an MCP module outside `core/connectors`.

    Matches real imports only (AST), since doors legitimately say "mcp" in text
    such as the `/mcp` command.
    """
    allowed = root / "core" / "connectors"
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.is_relative_to(allowed):
            continue
        imported = _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
        for target in MCP_MODULES:
            if any(m == target or m.startswith(f"{target}.") for m in imported):
                violations.append(f"{path}: forbidden import {target!r}")
    return violations


def test_cli_never_imports_core() -> None:
    """A door (`aldwin.cli`) must never import `aldwin.core`, Agentling or MCP."""
    assert (
        check_forbidden_imports(
            SRC_ALDWIN / "cli",
            ("aldwin.core", "agentling"),
        )
        == []
    )
    assert mcp_import_violations(SRC_ALDWIN / "cli") == []


def test_client_never_imports_core_or_agentling() -> None:
    """The client must never import `aldwin.core`, Agentling or MCP."""
    assert (
        check_forbidden_imports(
            SRC_ALDWIN / "client",
            ("aldwin.core", "agentling"),
        )
        == []
    )
    assert mcp_import_violations(SRC_ALDWIN / "client") == []


def test_only_core_connectors_imports_mcp() -> None:
    """`TC-76`: only `aldwin.core.connectors` imports `mcp`, `mcp_types` or `httpx2`."""
    assert mcp_import_violations(SRC_ALDWIN) == []


def test_mcp_guard_catches_planted_import(tmp_path: Path) -> None:
    """`TC-76`: an `import mcp` planted in a copy of the host fails the guard."""
    host = tmp_path / "aldwin" / "host"
    host.mkdir(parents=True)
    runner = (SRC_ALDWIN / "host" / "runner.py").read_text(encoding="utf-8")
    (host / "runner.py").write_text("import mcp\n" + runner, encoding="utf-8")
    connectors = tmp_path / "aldwin" / "core" / "connectors"
    connectors.mkdir(parents=True)
    (connectors / "connection.py").write_text("import mcp\n", encoding="utf-8")

    violations = mcp_import_violations(tmp_path / "aldwin")
    assert violations == [f"{host / 'runner.py'}: forbidden import 'mcp'"]


def test_connector_dependencies_pinned() -> None:
    """`TC-26`: Agentling 0.1.1 and `mcp` 2.x are pinned and `loopback` is a marker."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    deps = pyproject["project"]["dependencies"]
    assert "agentling>=0.1.1,<0.2" in deps
    assert "mcp>=2.3,<3" in deps
    markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("loopback:") for m in markers)

    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text("utf-8"))
    locked = {p["name"]: p["version"] for p in lock["package"]}
    assert Version(locked["agentling"]) >= Version("0.1.1")


def test_no_private_validate_calls() -> None:
    """`TC-27`: no private `_validate` calls and no old `validate_arguments` hook."""
    pattern = re.compile(r"\._validate\(|def validate_arguments\b")
    hits = [
        f"{path}:{n}"
        for path in sorted(SRC_ALDWIN.rglob("*.py"))
        for n, line in enumerate(path.read_text("utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []


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


def test_builtin_tools_match_builtin_tool_names(aldwin_home: Path) -> None:
    """`FR-8`: `builtin_tools()` returns the expected tool names."""
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


def test_protocol_doc_lists_connector_messages() -> None:
    """The protocol doc names every connector request, event and error code."""
    doc = (REPO_ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")
    assert doc.startswith("# Aldwin protocol (`v2`)")
    for name in (
        "connectors.list",
        "connectors.add",
        "connectors.remove",
        "connectors.enable",
        "connectors.disable",
        "connectors.reconnect",
        "connectors.tool_policy",
        "connectors.trust_hints",
        "connector.status",
        "connector.probe",
        "auth_required",
        "HOST_EVENT_RE",
    ):
        assert f"`{name}`" in doc, name
