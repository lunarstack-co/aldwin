"""Web UI scaffold and packaging guards."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"
STATIC_INDEX = REPO_ROOT / "src" / "aldwin" / "web" / "static" / "index.html"


def _package_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((WEB / "package.json").read_text("utf-8"))
    return data


def _git_ignored(path: str) -> bool:
    proc = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", path],
        cwd=REPO_ROOT,
        check=False,
    )
    return proc.returncode == 0


def test_web_static_page_committed() -> None:
    """TC-01: the package always carries a page, built or not."""
    assert (REPO_ROOT / "src" / "aldwin" / "web" / "__init__.py").is_file()
    html = STATIC_INDEX.read_text(encoding="utf-8")
    assert 'data-testid="aldwin-stub"' in html
    assert "Aldwin" in html


def test_gitignore_web_artifacts_and_static_tracked() -> None:
    """TC-02: Node and Cypress output is ignored; the packaged UI is not."""
    for path in (
        "web/node_modules/next/package.json",
        "web/.next/build-manifest.json",
        "web/out/index.html",
        "web/cypress/screenshots/a.png",
        "web/cypress/videos/a.mp4",
    ):
        assert _git_ignored(path), f"{path} should be ignored"
    assert not _git_ignored("src/aldwin/web/static/index.html")
    assert not _git_ignored("src/aldwin/web/static/_next/static/chunks/a.js")


def test_next_config_static_export_and_dev_proxy() -> None:
    """TC-03: static export, unoptimised images, and a dev-only API proxy."""
    config = (WEB / "next.config.ts").read_text(encoding="utf-8")
    assert 'output: "export"' in config
    assert re.search(r"images:\s*\{\s*unoptimized:\s*true\s*\}", config)
    assert "PHASE_DEVELOPMENT_SERVER" in config
    assert '"http://127.0.0.1:7841"' in config
    assert 'source: "/v1/:path*"' in config


def test_web_package_scripts_and_lockfile() -> None:
    """TC-04: npm lockfile and the scripts CI and contributors run."""
    assert (WEB / "package-lock.json").is_file()
    scripts = _package_json()["scripts"]
    for name in ("dev", "build", "postbuild", "lint", "typecheck", "cypress:run"):
        assert name in scripts, f"web/package.json is missing script {name!r}"
    assert scripts["postbuild"] == "node scripts/copy-export.mjs"


def test_web_ds_tokens_and_theme_hooks() -> None:
    """TC-07: docs DS tokens, class theme, button recipe and local Mona Sans."""
    css = (WEB / "src" / "app" / "globals.css").read_text(encoding="utf-8")
    for token in (":root {", ".dark {", "@theme inline", "--bg-base: #0d1117;"):
        assert token in css
    provider = (WEB / "src" / "components" / "theme-provider.tsx").read_text("utf-8")
    assert 'attribute="class"' in provider
    button = (WEB / "src" / "components" / "ui" / "button.tsx").read_text("utf-8")
    assert "export function buttonClass" in button

    fonts = WEB / "src" / "assets" / "fonts" / "mona-sans"
    for name in ("MonaSansVF.woff2", "MonaSansMonoVF.woff2", "OFL.txt"):
        assert (fonts / name).is_file()

    deps = {**_package_json()["dependencies"], **_package_json()["devDependencies"]}
    assert "lucide-react" in deps
    for banned in ("@radix-ui", "@headlessui", "shadcn", "next-auth"):
        assert not any(name.startswith(banned) for name in deps), banned


def test_stub_page_is_minimal() -> None:
    """TC-08: the scaffold page is brand only, with no app screens yet."""
    page = (WEB / "src" / "app" / "page.tsx").read_text(encoding="utf-8")
    assert 'data-testid="aldwin-stub"' in page
    assert "Aldwin" in page
    for later in ("onboarding", "ChatShell", "/v1/"):
        assert later not in page


def test_web_uses_cypress_not_playwright() -> None:
    """TC-25: the settled E2E runner is Cypress."""
    dev_deps = _package_json()["devDependencies"]
    assert "cypress" in dev_deps
    assert not any("playwright" in name for name in dev_deps)
    assert (WEB / "cypress" / "e2e" / "smoke.cy.ts").is_file()
