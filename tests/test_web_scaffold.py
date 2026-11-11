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
    styles = WEB / "src" / "styles"
    css = "".join(p.read_text(encoding="utf-8") for p in sorted(styles.glob("*.css")))
    for token in (":root {", ".dark {", "@theme inline", "--bg-base: #0d1117;"):
        assert token in css
    entry = (styles / "globals.css").read_text(encoding="utf-8")
    assert entry.index('@import "tailwindcss"') < entry.index('@import "./tokens.css"')
    provider = (WEB / "src" / "providers" / "theme-provider.tsx").read_text("utf-8")
    assert 'attribute="class"' in provider
    button = (WEB / "src" / "components" / "atoms" / "button.tsx").read_text("utf-8")
    assert "export function buttonClass" in button

    fonts = WEB / "src" / "assets" / "fonts" / "mona-sans"
    for name in ("MonaSansVF.woff2", "MonaSansMonoVF.woff2", "OFL.txt"):
        assert (fonts / name).is_file()

    deps = {**_package_json()["dependencies"], **_package_json()["devDependencies"]}
    assert "lucide-react" in deps
    for banned in ("@radix-ui", "@headlessui", "shadcn", "next-auth"):
        assert not any(name.startswith(banned) for name in deps), banned


def test_home_page_waits_on_the_gates_and_has_no_chat_yet() -> None:
    """TC-36: the home page shows that the gates are clear, not a chat screen."""
    page = (WEB / "src" / "app" / "page.tsx").read_text(encoding="utf-8")
    assert 'data-testid="aldwin-gates-clear"' in page
    assert "ChatShell" not in page
    layout = (WEB / "src" / "app" / "layout.tsx").read_text(encoding="utf-8")
    assert "<SetupProvider>" in layout


def test_gates_are_plain_pages_without_a_sign_in_layer() -> None:
    """TC-23, TC-24: no middleware, sign-in page, auth library or wizard store."""
    assert not (WEB / "src" / "middleware.ts").exists()
    assert not (WEB / "middleware.ts").exists()
    assert not list((WEB / "src" / "app").rglob("signin"))
    deps = {**_package_json()["dependencies"], **_package_json()["devDependencies"]}
    for banned in ("next-auth", "@auth/", "zustand"):
        assert not any(name.startswith(banned) for name in deps), banned
    pages = WEB / "src" / "app" / "onboarding"
    for step in ("name", "occupation", "personality", "connect"):
        assert (pages / step / "page.tsx").is_file(), step


def test_web_source_follows_the_folder_layout() -> None:
    """Components sit in atomic levels and shared code in its own top folder."""
    src = WEB / "src"
    levels = ("components/atoms", "components/molecules", "components/organisms")
    shared = (
        "layouts",
        "providers",
        "styles",
        "hooks",
        "utils",
        "data",
        "constants",
        "types",
    )
    for folder in (*levels, *shared):
        assert (src / folder).is_dir(), folder
    for gone in ("lib", "components/ui", "app/globals.css"):
        assert not (src / gone).exists(), gone
    for path in (src / "utils").glob("*"):
        assert path.is_dir(), f"utils/{path.name} should be a folder"


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def test_page_text_lives_in_data_files_not_in_tsx() -> None:
    """Every sentence in data/ is read from there, never repeated in the source."""
    src = WEB / "src"
    copy = [
        text
        for path in (src / "data").rglob("*.json")
        for text in _strings(json.loads(path.read_text(encoding="utf-8")))
        if " " in text
    ]
    assert copy, "data/ holds no text"
    code = "".join(
        path.read_text(encoding="utf-8")
        for pattern in ("*.ts", "*.tsx")
        for path in src.rglob(pattern)
    )
    lines = {line.strip() for line in code.splitlines()}

    def written(text: str) -> bool:
        quoted = any(f"{q}{text}{q}" in code for q in ('"', "'", "`"))
        return quoted or f">{text}<" in code or text in lines

    leaked = [text for text in copy if written(text)]
    assert not leaked, f"text written in source instead of data/: {leaked}"


def test_web_uses_cypress_not_playwright() -> None:
    """TC-25: the settled E2E runner is Cypress."""
    dev_deps = _package_json()["devDependencies"]
    assert "cypress" in dev_deps
    assert not any("playwright" in name for name in dev_deps)
    assert (WEB / "cypress" / "e2e" / "smoke.cy.ts").is_file()
