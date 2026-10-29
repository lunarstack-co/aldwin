"""Tests for the everyday-launch PyPI update nudge."""

from __future__ import annotations

import io
import json
import time
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
from typer.testing import CliRunner

from aldwin import __version__
from aldwin.cli.main import app
from aldwin.cli.render import ChatRenderer
from aldwin.cli.theme import create_console
from aldwin.client import bootstrap, update_available_nudge
from aldwin.core.home import atomic_write
from aldwin.core.settings import load_config
from aldwin.core.update_check import (
    FETCH_TIMEOUT_S,
    PYPI_JSON_URL,
    fetch_latest_version,
    format_nudge,
    is_newer,
    update_nudge_line,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_NUDGE = "Update available: 0.0.1 → 0.0.2 · uv tool upgrade aldwin"


class _FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _json_opener(payload: dict[str, Any], *, status: int = 200):
    body = json.dumps(payload).encode("utf-8")

    def opener(request: Request, timeout: float | None = None) -> _FakeResponse:
        _ = timeout
        if status >= 400:
            raise HTTPError(
                request.full_url,
                status,
                "error",
                hdrs=None,  # type: ignore[arg-type]
                fp=None,
            )
        return _FakeResponse(body, status=status)

    return opener


def test_update_nudge_line_when_newer() -> None:
    """Mock PyPI newer than installed yields the exact nudge line."""
    line = update_nudge_line(
        current_version="0.0.1",
        check_enabled=True,
        plain=False,
        fetch=lambda: "0.0.2",
    )
    assert line == EXPECTED_NUDGE


def test_update_nudge_line_same_version() -> None:
    """Same installed and PyPI versions yield no nudge."""
    assert (
        update_nudge_line(
            current_version="0.0.2",
            check_enabled=True,
            plain=False,
            fetch=lambda: "0.0.2",
        )
        is None
    )


def test_update_nudge_line_older_on_pypi() -> None:
    """PyPI older than installed yields no nudge."""
    assert (
        update_nudge_line(
            current_version="0.0.2",
            check_enabled=True,
            plain=False,
            fetch=lambda: "0.0.1",
        )
        is None
    )


def test_fetch_timeout_silent() -> None:
    """Timeout and URLError from fetch return None without raising."""

    def boom(request: Request, timeout: float | None = None) -> _FakeResponse:
        _ = request, timeout
        raise TimeoutError("timed out")

    assert fetch_latest_version(opener=boom) is None

    def url_boom(request: Request, timeout: float | None = None) -> _FakeResponse:
        _ = request, timeout
        raise URLError("offline")

    assert fetch_latest_version(opener=url_boom) is None
    assert (
        update_nudge_line(
            current_version="0.0.1",
            check_enabled=True,
            plain=False,
            fetch=lambda: fetch_latest_version(opener=boom),
        )
        is None
    )


def test_fetch_http_500_silent() -> None:
    """HTTP 500 from PyPI returns None."""
    opener = _json_opener({"info": {"version": "0.0.2"}}, status=500)
    assert fetch_latest_version(opener=opener) is None


def test_fetch_invalid_json_silent() -> None:
    """Invalid JSON body returns None."""

    def opener(request: Request, timeout: float | None = None) -> _FakeResponse:
        _ = request, timeout
        return _FakeResponse(b"not-json{")

    assert fetch_latest_version(opener=opener) is None


def test_fetch_missing_version_silent() -> None:
    """Missing info.version returns None."""
    assert fetch_latest_version(opener=_json_opener({"info": {}})) is None


def test_fetch_empty_or_null_version_silent() -> None:
    """Empty or null version returns None."""
    assert fetch_latest_version(opener=_json_opener({"info": {"version": ""}})) is None
    assert (
        fetch_latest_version(opener=_json_opener({"info": {"version": None}})) is None
    )


def test_is_newer_invalid_pep440() -> None:
    """Invalid PEP 440 strings are not treated as newer."""
    assert is_newer("not-a-version", "0.0.1") is False
    assert is_newer("0.0.2", "also-bad") is False
    assert (
        update_nudge_line(
            current_version="0.0.1",
            check_enabled=True,
            plain=False,
            fetch=lambda: "not-a-version",
        )
        is None
    )


def test_is_newer_prerelease() -> None:
    """Final release is newer than matching pre-release."""
    assert is_newer("0.0.2", "0.0.2a1") is True
    # pip and uv skip prereleases by default, so a prerelease on PyPI must not
    # trigger the nudge.
    assert is_newer("0.0.3b1", "0.0.2") is False


def test_format_nudge_copy() -> None:
    """Nudge copy includes both versions and the upgrade command."""
    text = format_nudge("1.2.3", "1.2.4")
    assert text == "Update available: 1.2.3 → 1.2.4 · uv tool upgrade aldwin"
    assert "changelog" not in text.lower()


def test_opt_out_skips_fetch() -> None:
    """Disabled check skips fetch and returns None."""
    calls: list[int] = []

    def fetch() -> str | None:
        calls.append(1)
        return "0.0.2"

    assert (
        update_nudge_line(
            current_version="0.0.1",
            check_enabled=False,
            plain=False,
            fetch=fetch,
        )
        is None
    )
    assert calls == []


def test_plain_skips_fetch() -> None:
    """Plain mode skips fetch and returns None."""
    calls: list[int] = []

    def fetch() -> str | None:
        calls.append(1)
        return "0.0.2"

    assert (
        update_nudge_line(
            current_version="0.0.1",
            check_enabled=True,
            plain=True,
            fetch=fetch,
        )
        is None
    )
    assert calls == []


def test_check_for_updates_default_true(aldwin_home: Path) -> None:
    """Missing check_for_updates key defaults to True."""
    assert load_config(aldwin_home).check_for_updates is True


def test_check_for_updates_false_loads(aldwin_home: Path) -> None:
    """check_for_updates: false loads as False."""
    atomic_write(aldwin_home / "config.yaml", "check_for_updates: false\n")
    assert load_config(aldwin_home).check_for_updates is False


def test_host_wrapper_respects_config_false(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Host wrapper with config false returns None without fetching."""
    atomic_write(aldwin_home / "config.yaml", "check_for_updates: false\n")
    calls: list[int] = []

    def fake_fetch(**kwargs: Any) -> str | None:
        _ = kwargs
        calls.append(1)
        return "9.9.9"

    monkeypatch.setattr(
        "aldwin.core.update_check.fetch_latest_version",
        fake_fetch,
    )
    assert (
        update_available_nudge(aldwin_home, current_version="0.0.1", plain=False)
        is None
    )
    assert calls == []


def test_host_wrapper_plain(aldwin_home: Path) -> None:
    """Host wrapper with plain=True returns None."""
    assert (
        update_available_nudge(aldwin_home, current_version="0.0.1", plain=True) is None
    )


def test_host_wrapper_happy_path(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Host wrapper returns the exact nudge when PyPI is newer."""
    monkeypatch.setattr(
        "aldwin.core.update_check.fetch_latest_version",
        lambda **_kwargs: "0.0.2",
    )
    assert (
        update_available_nudge(aldwin_home, current_version="0.0.1", plain=False)
        == EXPECTED_NUDGE
    )


def test_version_flag_does_not_call_pypi(monkeypatch: pytest.MonkeyPatch) -> None:
    """aldwin --version prints the version and never calls the update check."""
    calls: list[int] = []

    def boom(*args: Any, **kwargs: Any) -> str | None:
        _ = args, kwargs
        calls.append(1)
        raise AssertionError("update check must not run for --version")

    monkeypatch.setattr(
        "aldwin.core.update_check.fetch_latest_version",
        boom,
    )
    monkeypatch.setattr(
        "aldwin.host.in_process.update_available_nudge",
        boom,
    )
    res = CliRunner().invoke(app, ["--version"])
    assert res.exit_code == 0
    assert res.output.strip() == (
        f"aldwin {__version__} (protocol v2, agentling {metadata.version('agentling')})"
    )
    assert calls == []


def test_fetch_url_and_timeout() -> None:
    """Default URL, User-Agent, and 2s timeout are passed to the opener."""
    seen: dict[str, Any] = {}

    def opener(request: Request, timeout: float | None = None) -> _FakeResponse:
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["headers"] = dict(request.header_items())
        return _FakeResponse(json.dumps({"info": {"version": "0.0.2"}}).encode())

    assert fetch_latest_version(opener=opener) == "0.0.2"
    assert seen["url"] == PYPI_JSON_URL
    assert seen["timeout"] == FETCH_TIMEOUT_S
    assert FETCH_TIMEOUT_S == 2.0
    headers = {k.lower(): v for k, v in seen["headers"].items()}
    assert headers.get("user-agent") == "aldwin"


def test_fetch_request_sends_no_secrets() -> None:
    """Request has no Authorization, home path, or API key material."""
    seen: dict[str, Any] = {}

    def opener(request: Request, timeout: float | None = None) -> _FakeResponse:
        _ = timeout
        seen["url"] = request.full_url
        seen["headers"] = {k.lower(): v for k, v in request.header_items()}
        return _FakeResponse(json.dumps({"info": {"version": "0.0.2"}}).encode())

    assert fetch_latest_version(opener=opener) == "0.0.2"
    blob = json.dumps(seen).lower()
    assert "authorization" not in seen["headers"]
    assert seen["headers"].get("user-agent") == "aldwin"
    assert "sk-" not in blob
    assert "/users/" not in blob
    assert ".aldwin" not in blob


@pytest.mark.asyncio
async def test_welcome_path_prints_one_muted_line() -> None:
    """Everyday welcome helper prints exactly one Update available line."""
    from aldwin.cli.chat_screen import fetch_and_render_update_nudge

    buf = io.StringIO()
    console = create_console(plain=False, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=False)
    tip = "Try @ to pick a skill · /skills lists them"
    renderer.render_welcome(
        first_run=False,
        model="gpt-4o",
        display_home="~/.aldwin",
        tip=tip,
    )
    line = await fetch_and_render_update_nudge(
        renderer,
        home=Path("/tmp"),
        plain=False,
        current_version="0.0.1",
        skip_welcome=False,
        nudge_fn=lambda *_a, **_k: EXPECTED_NUDGE,
    )
    assert line == EXPECTED_NUDGE
    text = console.export_text()
    assert text.count("Update available:") == 1
    assert "0.0.1" in text and "0.0.2" in text
    assert "uv tool upgrade aldwin" in text
    assert tip in text


@pytest.mark.asyncio
async def test_skip_welcome_skips_update_check() -> None:
    """skip_welcome=True never calls the nudge function."""
    from aldwin.cli.chat_screen import fetch_and_render_update_nudge

    calls: list[int] = []

    def nudge(*args: Any, **kwargs: Any) -> str | None:
        _ = args, kwargs
        calls.append(1)
        return EXPECTED_NUDGE

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    line = await fetch_and_render_update_nudge(
        renderer,
        home=Path("/tmp"),
        plain=False,
        current_version="0.0.1",
        skip_welcome=True,
        nudge_fn=nudge,
    )
    assert line is None
    assert calls == []
    assert "Update available" not in console.export_text()


@pytest.mark.asyncio
async def test_hang_capped_at_two_seconds() -> None:
    """A slow nudge is capped at the timeout with no line and no error."""
    from aldwin.cli.chat_screen import fetch_and_render_update_nudge

    def slow(*args: Any, **kwargs: Any) -> str | None:
        _ = args, kwargs
        time.sleep(3.0)
        return EXPECTED_NUDGE

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    start = time.perf_counter()
    line = await fetch_and_render_update_nudge(
        renderer,
        home=Path("/tmp"),
        plain=False,
        current_version="0.0.1",
        skip_welcome=False,
        nudge_fn=slow,
        timeout=2.0,
    )
    elapsed = time.perf_counter() - start
    assert line is None
    assert elapsed < 2.5
    assert "Update available" not in console.export_text()


@pytest.mark.asyncio
async def test_update_nudge_propagates_cancellation() -> None:
    """CancelledError from the nudge wait is re-raised, not swallowed."""
    import asyncio

    from aldwin.cli.chat_screen import fetch_and_render_update_nudge

    def hang(*args: Any, **kwargs: Any) -> str | None:
        _ = args, kwargs
        time.sleep(5.0)
        return EXPECTED_NUDGE

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    task = asyncio.create_task(
        fetch_and_render_update_nudge(
            renderer,
            home=Path("/tmp"),
            plain=False,
            current_version="0.0.1",
            skip_welcome=False,
            nudge_fn=hang,
            timeout=10.0,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_no_new_runtime_deps_for_update_check() -> None:
    """Update check uses urllib plus the declared packaging dependency."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    joined = "\n".join(deps).lower()
    # `is_newer` needs packaging.version. No runtime package provides it
    # transitively (it enters only via pytest or pillow's tests extra), so it
    # must be declared or every comparison silently returns False.
    assert "packaging" in joined
    assert "requests" not in joined
    assert "urllib3" not in joined


def test_privacy_doc_mentions_pypi_and_opt_out() -> None:
    """The privacy doc documents the PyPI check and check_for_updates opt-out."""
    privacy = (REPO_ROOT / "docs" / "privacy.md").read_text(encoding="utf-8")
    assert "pypi.org/pypi/aldwin/json" in privacy
    assert "check_for_updates" in privacy


def test_bootstrap_does_not_fetch(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bootstrap() does not call the PyPI fetch."""
    _ = aldwin_home
    calls: list[int] = []

    def boom(**kwargs: Any) -> str | None:
        _ = kwargs
        calls.append(1)
        return "0.0.2"

    monkeypatch.setattr("aldwin.core.update_check.fetch_latest_version", boom)
    bootstrap(plain=True)
    assert calls == []


@pytest.mark.asyncio
async def test_tips_and_nudge_both_present() -> None:
    """Tip and update nudge can both appear."""
    from aldwin.cli.chat_screen import fetch_and_render_update_nudge

    tip = "Use /todo to review the task checklist"
    buf = io.StringIO()
    console = create_console(plain=False, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=False)
    renderer.render_welcome(
        first_run=False,
        model="gpt-4o",
        display_home="~/.aldwin",
        tip=tip,
    )
    await fetch_and_render_update_nudge(
        renderer,
        home=Path("/tmp"),
        plain=False,
        current_version="0.0.1",
        skip_welcome=False,
        nudge_fn=lambda *_a, **_k: EXPECTED_NUDGE,
    )
    text = console.export_text()
    assert tip in text
    assert EXPECTED_NUDGE in text


def test_wrapper_honors_plain_kwarg(aldwin_home: Path) -> None:
    """The wrapper returns None when the door passes plain=True."""
    assert (
        update_available_nudge(aldwin_home, current_version="0.0.1", plain=True) is None
    )


def test_fetch_success_path() -> None:
    """Happy path for fetch_latest_version with valid JSON."""
    opener = _json_opener({"info": {"version": "0.0.2"}})
    assert fetch_latest_version(opener=opener) == "0.0.2"


def test_render_update_nudge_sanitises_text() -> None:
    """render_update_nudge strips ANSI escapes and bidi controls before printing."""
    buf = io.StringIO()
    console = create_console(plain=False, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=False)
    line = (
        "Update \x1b[31mavailable\x1b[0m: 0.0.1 → \u202e0.0.2 · uv tool upgrade aldwin"
    )
    renderer.render_update_nudge(line)
    text = console.export_text()
    assert "\x1b" not in text
    assert "\u202e" not in text
    assert "0.0.1" in text and "0.0.2" in text and "uv tool upgrade aldwin" in text
