"""Architecture boundary guard tests."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
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


def test_web_door_never_imports_core() -> None:
    """The web door keeps the same walls as the CLI door."""
    assert (
        check_forbidden_imports(
            SRC_ALDWIN / "web",
            ("aldwin.core", "agentling"),
        )
        == []
    )
    assert mcp_import_violations(SRC_ALDWIN / "web") == []


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
    client_text = (SRC_ALDWIN / "client" / "client.py").read_text(encoding="utf-8")
    assert "list_available_models" not in client_text


def test_only_core_connectors_imports_mcp() -> None:
    """Only `aldwin.core.connectors` imports `mcp`, `mcp_types` or `httpx2`."""
    assert mcp_import_violations(SRC_ALDWIN) == []


def test_mcp_guard_catches_planted_import(tmp_path: Path) -> None:
    """An `import mcp` planted in a copy of the host fails the guard."""
    host = tmp_path / "aldwin" / "host"
    host.mkdir(parents=True)
    runner = (SRC_ALDWIN / "host" / "runner.py").read_text(encoding="utf-8")
    (host / "runner.py").write_text("import mcp\n" + runner, encoding="utf-8")
    connectors = tmp_path / "aldwin" / "core" / "connectors"
    connectors.mkdir(parents=True)
    (connectors / "connection.py").write_text("import mcp\n", encoding="utf-8")

    violations = mcp_import_violations(tmp_path / "aldwin")
    assert violations == [f"{host / 'runner.py'}: forbidden import 'mcp'"]


def test_startup_never_loads_web_server_modules() -> None:
    """The terminal never loads FastAPI, Starlette or uvicorn; `aldwin web` does."""
    probe = (
        "import sys, aldwin.cli.main, aldwin.client, aldwin.web.lock\n"
        "heavy = ('fastapi', 'starlette', 'uvicorn', 'aldwin.web.app',\n"
        "         'aldwin.web.server', 'aldwin.web.cli')\n"
        "print(sorted(m for m in sys.modules\n"
        "             if any(m == h or m.startswith(h + '.') for h in heavy)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


def test_startup_never_loads_mcp_or_sign_in() -> None:
    """Sign-in code and the `mcp` SDK load only when a connector needs them."""
    probe = (
        "import sys, aldwin.cli.main, aldwin.host.runner, aldwin.client\n"
        "lazy = ('mcp', 'mcp_types', 'aldwin.core.connectors.oauth',\n"
        "        'aldwin.core.connectors.sign_in',\n"
        "        'aldwin.core.connectors.token_storage')\n"
        "print(sorted(m for m in sys.modules if m in lazy or m.startswith('mcp.')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


def test_connector_dependencies_pinned() -> None:
    """Agentling 0.2.x and `mcp` 2.x are pinned and `openai` is absent from lock."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    deps = pyproject["project"]["dependencies"]
    assert "agentling>=0.2,<0.3" in deps
    assert "mcp>=2.3,<3" in deps
    markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("loopback:") for m in markers)

    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text("utf-8"))
    locked = {p["name"]: p["version"] for p in lock["package"]}
    assert Version(locked["agentling"]) >= Version("0.2.0")
    assert "openai" not in locked


def test_doors_never_import_providers() -> None:
    """A door (`aldwin.cli`) and `client` must never import `aldwin.core.providers`."""
    assert (
        check_forbidden_imports(
            SRC_ALDWIN / "cli",
            ("aldwin.core.providers",),
        )
        == []
    )
    assert (
        check_forbidden_imports(
            SRC_ALDWIN / "client",
            ("aldwin.core.providers",),
        )
        == []
    )


def test_no_private_agentling_imports_in_src() -> None:
    """No file under `src/aldwin` imports private Agentling modules or attributes."""
    forbidden = ("agentling.models", "agentling.agent")
    violations: list[str] = []
    private_reach_in = re.compile(r"\._(interrupt|base_tools)\b")
    for path in sorted(SRC_ALDWIN.rglob("*.py")):
        text = path.read_text("utf-8")
        tree = ast.parse(text, filename=str(path))
        imported = _imported_modules(tree)
        for target in forbidden:
            if any(m == target or m.startswith(f"{target}.") for m in imported):
                violations.append(f"{path}: forbidden import {target!r}")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == "agentling" or node.module.startswith("agentling."))
            ):
                for alias in node.names:
                    if alias.name.startswith("_"):
                        sym = f"{node.module}.{alias.name}"
                        violations.append(f"{path}: forbidden private import {sym}")
        for n, line in enumerate(text.splitlines(), 1):
            if private_reach_in.search(line):
                violations.append(
                    f"{path}:{n}: forbidden reach-in to Agentling private attribute"
                )
    assert violations == []


def test_no_private_validate_calls() -> None:
    """No private `_validate` calls and no old `validate_arguments` hook."""
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
            ("aldwin.core", "aldwin.host", "aldwin.client", "aldwin.cli", "agentling"),
        )
        == []
    )

    display_py = SRC_ALDWIN / "protocol" / "display.py"
    assert display_py.is_file()
    imported = _imported_modules(
        ast.parse(display_py.read_text(encoding="utf-8"), filename=str(display_py))
    )
    allowed_roots = {"__future__", "functools", "typing", "pydantic", "aldwin"}
    for mod in imported:
        root = mod.split(".")[0]
        assert root in allowed_roots, f"Unexpected import {mod!r} in {display_py}"
        assert not mod.startswith("aldwin.") or mod.startswith("aldwin.protocol")


def test_core_never_imports_protocol_host_client_or_cli() -> None:
    """The brain (`aldwin.core`) must never import protocol, host, client, or cli."""
    assert (
        check_forbidden_imports(
            SRC_ALDWIN / "core",
            ("aldwin.protocol", "aldwin.host", "aldwin.client", "aldwin.cli"),
        )
        == []
    )


def test_core_providers_never_imports_doors() -> None:
    """`aldwin.core.providers` must never import doors or host/protocol."""
    forbidden = (
        "aldwin.cli",
        "aldwin.client",
        "aldwin.host",
        "aldwin.protocol",
    )
    assert check_forbidden_imports(SRC_ALDWIN / "core" / "providers", forbidden) == []


def test_no_vendor_sdks_imported_in_src() -> None:
    """No module under `src/aldwin` imports vendor SDK packages."""
    vendor_sdks = ("openai", "anthropic", "google", "ollama")
    violations: list[str] = []
    for path in sorted(SRC_ALDWIN.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        imported = _imported_modules(tree)
        for mod in imported:
            if any(mod == sdk or mod.startswith(f"{sdk}.") for sdk in vendor_sdks):
                violations.append(f"{path}: forbidden vendor SDK import {mod!r}")
    assert violations == []


def test_builtin_tools_match_builtin_tool_names(aldwin_home: Path) -> None:
    """`builtin_tools()` returns the expected tool names."""
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
    """`check_forbidden_imports` flags a deliberate bad import."""
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
    """CI runs Python 3.11 to 3.13, a security job and a coverage upload."""
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
        "--cov=aldwin.core.connectors" in r and "--cov-fail-under=90" in r for r in runs
    ), "the connectors package keeps its own 90% gate"
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


def _ci_jobs() -> dict[str, Any]:
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    jobs: dict[str, Any] = yaml.safe_load(ci_path.read_text(encoding="utf-8"))["jobs"]
    return jobs


def test_ci_workflow_has_web_job() -> None:
    """A separate `web` job lints, type-checks, builds and runs Playwright."""
    web = _ci_jobs()["web"]
    assert web["runs-on"] == "ubuntu-latest"
    assert web["defaults"]["run"]["working-directory"] == "web"

    steps: list[dict[str, Any]] = web["steps"]
    node = next(s for s in steps if s.get("uses", "").startswith("actions/setup-node@"))
    assert node["with"]["node-version"] == "22"
    runs = [s.get("run", "") for s in steps]
    for command in (
        "npm ci",
        "npm run lint",
        "npm run typecheck",
        "npm run build",
        "npm run test:pw",
        "npm run test:pw:timing",
        "npm run test:pw:kit",
    ):
        assert command in runs, f"web job is missing {command!r}"
    # npm ci does not fetch browsers; the job installs every engine the suite runs.
    browsers = next(r for r in runs if "npx playwright install" in r)
    for engine in ("chromium", "firefox", "webkit"):
        assert engine in browsers, engine
    assert runs.index("npm run build") < runs.index("npm run test:pw")
    assert not any("cypress" in s.get("uses", "") for s in steps)


def test_ci_test_job_remains_node_free() -> None:
    """The Python matrix never needs Node; the web job owns it."""
    for step in _ci_jobs()["test"]["steps"]:
        uses = step.get("uses", "")
        run = step.get("run", "")
        assert not uses.startswith("actions/setup-node@")
        assert "cypress" not in uses
        for tool in ("npm ", "npx ", "node "):
            assert tool not in run, f"test job runs {tool.strip()!r}"


def test_manual_test_doc_exists_and_covers_all_fr11_steps() -> None:
    """`docs/manual-test.md` covers all required steps."""
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
        "connectors.login",
        "connectors.logout",
        "connectors.auth_paste",
        "connectors.auth_token",
        "connector.auth",
        "needs_auth",
    ):
        assert f"`{name}`" in doc, name


def test_manual_test_doc_has_connectors_section() -> None:
    doc = (REPO_ROOT / "docs" / "manual-test.md").read_text(encoding="utf-8")
    assert "### Step 14: Connectors (MCP)" in doc
    assert "aldwin connectors add github" in doc
    assert "### Step 15: Connectors: sign in with your browser" in doc
    assert "aldwin connectors login linear --paste" in doc


def test_protocol_doc_lists_displays() -> None:
    """`docs/protocol.md` lists the 9 display kinds, caps, `call_id`, and `context`."""
    doc = (REPO_ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")
    for token in (
        "`terminal`",
        "`diff`",
        "`files`",
        "`search`",
        "`page`",
        "`table`",
        "`memory`",
        "`denied`",
        "`grant`",
        "`400`",
        "`65536`",
        "`600`",
        "`300`",
        "`10`",
        "`200`",
        "`20`",
        "`call_id`",
        "`context`",
    ):
        assert token in doc, f"Missing {token} in docs/protocol.md"
