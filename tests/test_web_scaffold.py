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
    """TC-02: Node and Playwright output is ignored; the packaged UI is not."""
    for path in (
        "web/node_modules/next/package.json",
        "web/.next/build-manifest.json",
        "web/out/index.html",
        "web/test-results/a/trace.zip",
        "web/playwright-report/index.html",
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
    for name in (
        "dev",
        "build",
        "postbuild",
        "lint",
        "typecheck",
        "test:pw",
        "test:pw:kit",
    ):
        assert name in scripts, f"web/package.json is missing script {name!r}"
    assert scripts["postbuild"] == "node scripts/copy-export.mjs"


def test_web_ds_tokens_and_theme_hooks() -> None:
    """TC-07: assistant-ui tokens, class theme, kit button and local fonts."""
    styles = WEB / "src" / "styles"
    css = "".join(p.read_text(encoding="utf-8") for p in sorted(styles.glob("*.css")))
    for token in (
        ":root {",
        ".dark {",
        "@theme inline",
        "--background: oklch(0.17 0.003 var(--tint));",
    ):
        assert token in css
    entry = (styles / "globals.css").read_text(encoding="utf-8")
    assert entry.index('@import "tailwindcss"') < entry.index('@import "./tokens.css"')
    provider = (WEB / "src" / "providers" / "theme-provider.tsx").read_text("utf-8")
    assert 'attribute="class"' in provider
    button = (WEB / "src" / "components" / "ui" / "button.tsx").read_text("utf-8")
    assert "buttonVariants" in button

    fonts = WEB / "src" / "assets" / "fonts"
    for name in (
        "public-sans/PublicSansVF-latin.woff2",
        "public-sans/OFL.txt",
        "jetbrains-mono/JetBrainsMonoVF-latin.woff2",
        "jetbrains-mono/OFL.txt",
    ):
        assert (fonts / name).is_file(), name

    deps = {**_package_json()["dependencies"], **_package_json()["devDependencies"]}
    for needed in ("lucide-react", "@assistant-ui/react", "@base-ui/react"):
        assert needed in deps, needed
    for banned in ("@radix-ui", "@headlessui", "next-auth"):
        assert not any(name.startswith(banned) for name in deps), banned


def test_home_page_mounts_the_chat_shell_behind_the_gates() -> None:
    """TC-01, TC-20: `/` and `/c/` share the chat shell, behind the gates."""
    chat = WEB / "src" / "app" / "(chat)"
    assert (chat / "page.tsx").is_file()
    assert (chat / "c" / "page.tsx").is_file()
    shell = (chat / "layout.tsx").read_text(encoding="utf-8")
    assert 'data-testid="aldwin-gates-clear"' in shell
    assert "<ChatShell>" in shell
    layout = (WEB / "src" / "app" / "layout.tsx").read_text(encoding="utf-8")
    assert "<SetupProvider>" in layout
    chat_layout = (WEB / "src" / "layouts" / "chat-layout.tsx").read_text("utf-8")
    assert 'data-testid="aldwin-chat-shell"' in chat_layout


def test_gates_are_plain_pages_without_a_sign_in_layer() -> None:
    """TC-23, TC-24: no middleware, sign-in page or auth library."""
    assert not (WEB / "src" / "middleware.ts").exists()
    assert not (WEB / "middleware.ts").exists()
    assert not list((WEB / "src" / "app").rglob("signin"))
    deps = {**_package_json()["dependencies"], **_package_json()["devDependencies"]}
    for banned in ("next-auth", "@auth/"):
        assert not any(name.startswith(banned) for name in deps), banned
    pages = WEB / "src" / "app" / "onboarding"
    for step in ("name", "occupation", "personality", "connect"):
        assert (pages / step / "page.tsx").is_file(), step


def test_web_source_follows_the_folder_layout() -> None:
    """Kit, registry and app components sit apart; shared code has its own folder."""
    src = WEB / "src"
    levels = (
        "components/ui",
        "components/assistant-ui",
        "components/brand",
        "components/organisms",
    )
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
    for gone in ("lib", "components/atoms", "components/molecules", "app/globals.css"):
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


def test_web_uses_playwright_not_cypress() -> None:
    """TC-25: the E2E runner is Playwright, with its fake host."""
    dev_deps = _package_json()["devDependencies"]
    assert "@playwright/test" in dev_deps
    assert not any("cypress" in name for name in dev_deps)
    assert (WEB / "playwright.config.ts").is_file()
    assert (WEB / "playwright" / "smoke.spec.ts").is_file()
    assert (WEB / "playwright" / "fixtures" / "fake-host.ts").is_file()
    assert not (WEB / "cypress").exists()
