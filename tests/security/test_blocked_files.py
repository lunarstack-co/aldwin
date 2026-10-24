"""blocked secret paths are refused or hidden across tools."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from security._helpers import call_fail


def _plant_blocked(root: Path, home: Path) -> list[tuple[str, Path]]:
    """Create one fixture file per blocked-path category; return (label, path)."""
    user = Path.home()
    fixtures: list[tuple[str, Path]] = []

    def plant(label: str, path: Path, text: str = "SECRET\n") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        fixtures.append((label, path))

    plant("ssh", user / ".ssh" / "id_rsa")
    plant("aws", user / ".aws" / "credentials")
    plant("gnupg", user / ".gnupg" / "pubring.kbx")
    plant("kube", user / ".kube" / "config")
    plant("docker", user / ".docker" / "config.json")
    plant("pem", root / "server.pem")
    plant("ed25519", root / "deploy" / "id_ed25519")
    plant("netrc", root / ".netrc")
    plant("npmrc", root / ".npmrc")
    plant("gitcred", root / ".git-credentials")
    plant("env", root / ".env")
    plant("env_local", root / ".env.local")
    plant("envrc", root / ".envrc")
    plant("aldwin_secrets", home / "secrets.yaml", "openai_api_key: SECRET\n")
    return fixtures


async def test_blocked_categories_refused_by_read_tools(
    security_tools: SimpleNamespace,
    canary_home: Path,
) -> None:
    """Each blocked category is refused by `read_file` with blocked_path text."""
    fixtures = _plant_blocked(security_tools.root, security_tools.home)
    for label, path in fixtures:
        security_tools.session.reset_turn()
        if path == security_tools.home / "secrets.yaml":
            rel_or_abs = str(path)
        elif path.is_relative_to(security_tools.root):
            rel_or_abs = str(path.relative_to(security_tools.root))
        else:
            rel_or_abs = str(path)
        msg = await call_fail(
            security_tools.tools["read_file"],
            {"path": rel_or_abs},
        )
        assert "may hold secrets" in msg or "blocked" in msg.lower(), (label, msg)
        assert "SECRET" not in msg, label


async def test_env_example_readable_env_local_blocked(
    security_tools: SimpleNamespace,
) -> None:
    """`.env.example` is readable; `.env.local` is not."""
    (security_tools.root / ".env.example").write_text("OK=1\n", encoding="utf-8")
    (security_tools.root / ".env.local").write_text("NO=1\n", encoding="utf-8")
    ok = await security_tools.tools["read_file"].call({"path": ".env.example"})
    assert "OK=1" in str(ok)
    bad = await call_fail(
        security_tools.tools["read_file"],
        {"path": ".env.local"},
    )
    assert "may hold secrets" in bad


async def test_grep_never_returns_env_contents(
    security_tools: SimpleNamespace,
    canary_home: Path,
) -> None:
    """grep does not return `.env` lines; hidden footer present."""
    from canary import CANARY

    (security_tools.root / "ok.txt").write_text(f"visible {CANARY}\n", encoding="utf-8")
    result = await security_tools.tools["grep"].call({"pattern": CANARY, "path": "."})
    text = str(result)
    assert ".env:" not in text
    assert "may hold secrets" in text or "hidden" in text
    # Visible non-blocked file may still match.
    assert "ok.txt" in text or "visible" in text


async def test_glob_and_list_hide_blocked(
    security_tools: SimpleNamespace,
) -> None:
    """`glob` / `list_dir` hide blocked names and show a hidden count."""
    (security_tools.root / ".env").write_text("X=1\n", encoding="utf-8")
    (security_tools.root / "ok.txt").write_text("y\n", encoding="utf-8")
    listed = await security_tools.tools["list_dir"].call({"path": "."})
    found = await security_tools.tools["glob"].call({"pattern": "*"})
    for body in (str(listed), str(found)):
        assert ".env" not in body.split("hidden")[0] or "hidden" in body
        assert "may hold secrets" in body


async def test_aldwin_tmp_readable_not_writable(
    security_tools: SimpleNamespace,
) -> None:
    """`~/.aldwin/tmp/*.txt` readable; writes blocked."""
    tmp = security_tools.home / "tmp" / "abc.txt"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("middle\n", encoding="utf-8")
    tmp.chmod(0o600)
    read = await security_tools.tools["read_file"].call({"path": str(tmp)})
    assert "middle" in str(read)
    msg = await call_fail(
        security_tools.tools["write_file"],
        {"path": str(tmp), "content": "x\n"},
    )
    assert "blocked" in msg.lower() or "Denied" in msg or "Not run" in msg
    assert tmp.read_text(encoding="utf-8") == "middle\n"


async def test_git_read_allowed_write_blocked(
    security_tools: SimpleNamespace,
) -> None:
    """`.git/**` reads allowed; writes blocked."""
    git_cfg = security_tools.root / ".git" / "config"
    try:
        git_cfg.parent.mkdir(parents=True, exist_ok=True)
        git_cfg.write_text("[core]\n\tbare = false\n", encoding="utf-8")
    except PermissionError:
        pytest.skip("sandbox blocks creating .git/")
    read = await security_tools.tools["read_file"].call({"path": ".git/config"})
    assert "bare" in str(read)
    security_tools.session.mark_read(
        security_tools.workspace.resolve_safe(".git/config").path
    )
    msg = await call_fail(
        security_tools.tools["write_file"],
        {"path": ".git/config", "content": "hacked\n"},
    )
    assert "git" in msg.lower() or "blocked" in msg.lower()
    assert "bare = false" in git_cfg.read_text(encoding="utf-8")


async def test_build_files_ask_never_grantable(
    security_tools: SimpleNamespace,
    set_answer,
) -> None:
    """`BUILD_FILES` always ask and never offer session grant."""
    path = security_tools.root / "pyproject.toml"
    path.write_text("[project]\nname='x'\n", encoding="utf-8")
    await security_tools.tools["read_file"].call({"path": "pyproject.toml"})
    set_answer("yes")
    out = await security_tools.tools["write_file"].call(
        {"path": "pyproject.toml", "content": "[project]\nname='y'\n"}
    )
    assert out.ok is True
    assert len(security_tools.asks) >= 1
    req = security_tools.asks[-1]
    assert req.grantable is False
