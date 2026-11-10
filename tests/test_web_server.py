"""The ``aldwin web`` app, socket binding and process lifecycle."""

from __future__ import annotations

import signal
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from aldwin.web import server as server_mod
from aldwin.web.app import (
    STATIC_DIR,
    StaticFilesMissingError,
    create_app,
)
from aldwin.web.auth import COOKIE_NAME, Authenticator
from aldwin.web.lock import WebProcessLock

REPO_ROOT = Path(__file__).resolve().parent.parent
PORT = 7841
BASE = f"http://127.0.0.1:{PORT}"
STUB = '<main data-testid="aldwin-test-page">Aldwin</main>'


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    path = tmp_path / "static"
    (path / "_next").mkdir(parents=True)
    (path / "index.html").write_text(STUB, encoding="utf-8")
    (path / "_next" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return path


@pytest.fixture
def auth() -> Authenticator:
    return Authenticator.mint("127.0.0.1", PORT)


@pytest.fixture
def client(auth: Authenticator, static_dir: Path) -> Iterator[TestClient]:
    app = create_app(
        authenticator=auth,
        home_display="~/.aldwin",
        version="9.9.9",
        static_dir=static_dir,
    )
    with TestClient(app, base_url=BASE) as test_client:
        yield test_client


def _sign_in(client: TestClient, auth: Authenticator) -> None:
    response = client.get(f"/?t={auth.token}")
    assert response.status_code == 200


def test_bootstrap_then_health_and_page(
    client: TestClient, auth: Authenticator
) -> None:
    """TC-01: the launch link signs in, then health and the page load."""
    _sign_in(client, auth)
    health = client.get("/v1/health")
    assert health.status_code == 200
    assert health.json() == {"ok": True, "version": "9.9.9"}
    page = client.get("/")
    assert page.status_code == 200
    assert "aldwin-test-page" in page.text


def test_rest_without_cookie_is_401(client: TestClient) -> None:
    """TC-02: every ``/v1`` path needs the session cookie."""
    for path in ("/v1/health", "/v1/home", "/v1/unknown", "/v1"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert "aldwin web" in response.json()["detail"]


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "http://127.0.0.1:9999"},
        {"Host": "evil.example:7841"},
        {"Host": "127.0.0.1:9999"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_foreign_origin_or_host_is_403(
    client: TestClient, auth: Authenticator, headers: dict[str, str]
) -> None:
    """TC-03: a valid cookie from a foreign page or rebound name is refused."""
    client.cookies.set(COOKIE_NAME, auth.token)
    response = client.get("/v1/health", headers=headers)
    assert response.status_code == 403


def test_bootstrap_sets_strict_cookie_and_strips_token(
    client: TestClient, auth: Authenticator
) -> None:
    """TC-05: Set-Cookie flags are exact and the redirect drops ``?t=``."""
    response = client.get(f"/?t={auth.token}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    parts = [part.strip() for part in cookie.split(";")]
    assert parts[0] == f"{COOKIE_NAME}={auth.token}"
    lowered = {part.lower() for part in parts[1:]}
    assert lowered == {"httponly", "path=/", "samesite=strict"}
    assert "secure" not in lowered


def test_bad_token_sets_no_cookie(client: TestClient) -> None:
    """TC-08: a wrong token is refused and leaves no cookie."""
    response = client.get("/?t=wrong", follow_redirects=False)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert client.get("/v1/health").status_code == 401


def test_bootstrap_from_foreign_host_is_403(
    client: TestClient, auth: Authenticator
) -> None:
    response = client.get(
        f"/?t={auth.token}",
        headers={"Host": "evil.example:7841"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_home_returns_home_and_version(client: TestClient, auth: Authenticator) -> None:
    """TC-09: the signed-in page can read the home folder and version."""
    _sign_in(client, auth)
    response = client.get("/v1/home")
    assert response.status_code == 200
    assert response.json() == {"home": "~/.aldwin", "version": "9.9.9"}


def test_page_and_assets_load_without_cookie(client: TestClient) -> None:
    """TC-10 and TC-29: the UI files never need the cookie."""
    page = client.get("/")
    assert page.status_code == 200
    assert "aldwin-test-page" in page.text
    asset = client.get("/_next/app.js")
    assert asset.status_code == 200
    assert client.get("/missing.js").status_code == 404


def test_missing_origin_ok_when_host_matches(
    client: TestClient, auth: Authenticator
) -> None:
    """TC-11: a same-origin GET without Origin passes the guard."""
    client.cookies.set(COOKIE_NAME, auth.token)
    assert client.get("/v1/health").status_code == 200
    response = client.get("/v1/health", headers={"Origin": BASE})
    assert response.status_code == 200
    for fetch_site in ("same-origin", "none"):
        response = client.get("/v1/health", headers={"Sec-Fetch-Site": fetch_site})
        assert response.status_code == 200, fetch_site
    localhost = client.get(
        "/v1/health",
        headers={"Host": f"localhost:{PORT}", "Origin": f"http://localhost:{PORT}"},
    )
    assert localhost.status_code == 200


def test_wrong_cookie_value_is_401(client: TestClient) -> None:
    """TC-12: a cookie from another run does not work."""
    client.cookies.set(COOKIE_NAME, "from-an-old-run")
    assert client.get("/v1/health").status_code == 401


def test_skeleton_serves_only_health_and_home(
    client: TestClient, auth: Authenticator
) -> None:
    """TC-30: no conversation, settings or WebSocket API exists yet."""
    _sign_in(client, auth)
    for path in ("/v1/conversations", "/v1/settings", "/v1/ws", "/v1/ws-ticket"):
        assert client.get(path).status_code == 404, path
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/ws"):
            pass
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    sources = "".join(p.read_text("utf-8") for p in STATIC_DIR.parent.glob("*.py"))
    assert "AldwinHost" not in sources


def test_non_ascii_token_or_cookie_is_refused_not_500(client: TestClient) -> None:
    """A token or cookie with bytes outside ASCII gets the usual 401."""
    assert client.get("/?t=%C3%A9", follow_redirects=False).status_code == 401
    client.cookies.set(COOKIE_NAME, "caf%C3%A9")
    assert client.get("/v1/health").status_code == 401


def test_websocket_under_v1_is_refused_even_with_cookie(
    auth: Authenticator, static_dir: Path
) -> None:
    """A WebSocket route under ``/v1`` is closed until it has its own check."""
    app = create_app(
        authenticator=auth, home_display="~", version="1", static_dir=static_dir
    )

    @app.websocket("/v1/echo")
    async def echo(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("open")

    with TestClient(app, base_url=BASE) as test_client:
        test_client.cookies.set(COOKIE_NAME, auth.token)
        with pytest.raises(WebSocketDisconnect) as closed:
            with test_client.websocket_connect("/v1/echo") as ws:
                ws.receive_text()
    assert closed.value.code == 1008


def test_missing_static_tree_fails_fast(auth: Authenticator, tmp_path: Path) -> None:
    """TC-31: a package without the UI says how to build it."""
    with pytest.raises(StaticFilesMissingError, match="npm run build"):
        create_app(
            authenticator=auth,
            home_display="~",
            version="1",
            static_dir=tmp_path / "nope",
        )


def test_packaged_static_page_is_served(auth: Authenticator) -> None:
    """TC-01: the page shipped in the package is what ``/`` serves."""
    app = create_app(authenticator=auth, home_display="~", version="1")
    with TestClient(app, base_url=BASE) as test_client:
        response = test_client.get("/")
    assert response.status_code == 200
    assert response.text == (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _free_port(host: str = "127.0.0.1") -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        port: int = probe.getsockname()[1]
    return port


@pytest.mark.loopback
def test_default_bind_uses_the_preferred_port_when_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-21: with no ``--port``, the default port is used when it is free."""
    preferred = _free_port()
    monkeypatch.setattr(server_mod, "DEFAULT_PORT", preferred)
    sock = server_mod.bind_socket("127.0.0.1", None)
    try:
        assert sock.getsockname()[1] == preferred
    finally:
        sock.close()


@pytest.mark.loopback
def test_busy_default_port_falls_back_to_a_free_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-22: a busy default port does not stop the server starting."""
    busy = server_mod.bind_socket("127.0.0.1", 0)
    preferred = busy.getsockname()[1]
    monkeypatch.setattr(server_mod, "DEFAULT_PORT", preferred)
    try:
        sock = server_mod.bind_socket("127.0.0.1", None)
        try:
            assert sock.getsockname()[1] not in (preferred, 0)
        finally:
            sock.close()
    finally:
        busy.close()


@pytest.mark.loopback
def test_busy_explicit_port_raises() -> None:
    busy = server_mod.bind_socket("127.0.0.1", 0)
    try:
        with pytest.raises(OSError):
            server_mod.bind_socket("127.0.0.1", busy.getsockname()[1])
    finally:
        busy.close()


@pytest.mark.loopback
@pytest.mark.parametrize("host", ["localhost", "::1"])
def test_localhost_and_ipv6_loopback_bind(host: str) -> None:
    """TC-23: ``localhost`` binds IPv4 loopback; ``::1`` binds IPv6."""
    if host == "::1" and not socket.has_ipv6:
        pytest.skip("IPv6 is not available")
    try:
        sock = server_mod.bind_socket(host, 0)
    except OSError:
        pytest.skip(f"cannot bind {host} here")
    try:
        expected = "::1" if host == "::1" else "127.0.0.1"
        assert sock.getsockname()[0] == expected
    finally:
        sock.close()


_RUN_WEB = (
    "import sys\n"
    "from aldwin.cli.main import app\n"
    "sys.argv = ['aldwin', 'web', '--no-open', *sys.argv[1:]]\n"
    "app()\n"
)


def _start_web(*args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-u", "-c", _RUN_WEB, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_until_ready(proc: subprocess.Popen[str]) -> list[str]:
    assert proc.stdout is not None
    lines: list[str] = []
    while True:
        line = proc.stdout.readline()
        if not line:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(f"aldwin web exited early: {lines} {stderr}")
        lines.append(line.strip())
        if line.startswith("Press Ctrl-C"):
            return lines


@pytest.mark.loopback
@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT])
def test_second_web_refused_and_signal_releases_lock(
    aldwin_home: Path, stop_signal: signal.Signals
) -> None:
    """TC-07: a second live server is refused; stopping releases the lock."""
    port = _free_port()
    first = _start_web("--port", str(port))
    try:
        lines = _wait_until_ready(first)
        assert lines[0] == f"Aldwin web is running at http://127.0.0.1:{port}"
        assert f"http://127.0.0.1:{port}/?t=" in lines[1]
        record = WebProcessLock(aldwin_home).read()
        assert record is not None and record["pid"] == first.pid
        assert record["port"] == port

        second = subprocess.run(
            [sys.executable, "-c", _RUN_WEB, "--port", str(_free_port())],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert second.returncode == 1
        assert f"already running at http://127.0.0.1:{port}" in second.stderr
        assert f"pid {first.pid}" in second.stderr

        first.send_signal(stop_signal)
        assert first.wait(timeout=30) == 0
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=30)
        for stream in (first.stdout, first.stderr):
            if stream is not None:
                stream.close()
    assert WebProcessLock(aldwin_home).read() is None
    lock = WebProcessLock(aldwin_home)
    assert lock.try_acquire(host="127.0.0.1", port=port)
    lock.release()


@pytest.mark.loopback
def test_run_web_holds_the_lock_while_serving_then_releases(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command prints the launch link, opens the browser and cleans up."""
    from aldwin.web import cli as web_cli

    opened: list[str] = []
    seen: list[dict[str, object] | None] = []

    def fake_serve(_app: object, sock: socket.socket, on_ready: object) -> None:
        assert callable(on_ready)
        on_ready()
        seen.append(WebProcessLock(aldwin_home).read())
        sock.close()

    monkeypatch.setattr(web_cli, "serve", fake_serve)
    monkeypatch.setattr(web_cli.webbrowser, "open", opened.append)

    code = web_cli.run_web(host="127.0.0.1", port=0, open_browser=True, workspace=None)

    assert code == 0
    record = seen[0]
    assert record is not None and isinstance(record["port"], int)
    out = capsys.readouterr().out
    assert f"running at http://127.0.0.1:{record['port']}" in out
    assert opened and opened[0].startswith(f"http://127.0.0.1:{record['port']}/?t=")
    assert opened[0] in out
    assert WebProcessLock(aldwin_home).read() is None


@pytest.mark.loopback
def test_run_web_without_ui_files_exits_1_and_releases_lock(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """TC-31: a missing UI stops startup with the build hint."""
    from functools import partial

    from aldwin.web import cli as web_cli

    monkeypatch.setattr(
        web_cli, "create_app", partial(create_app, static_dir=tmp_path / "nope")
    )

    code = web_cli.run_web(host="127.0.0.1", port=0, open_browser=False, workspace=None)

    assert code == 1
    assert "npm run build" in capsys.readouterr().err
    assert WebProcessLock(aldwin_home).read() is None


def test_run_web_bad_workspace_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    from aldwin.web import cli as web_cli

    code = web_cli.run_web(
        host="127.0.0.1", port=None, open_browser=False, workspace="/"
    )
    assert code == 2
    assert "workspace too broad" in capsys.readouterr().err


def test_privacy_doc_lists_web_lock_and_session_power() -> None:
    """TC-28: the privacy page names the lock and what the session cookie grants."""
    text = (REPO_ROOT / "docs" / "privacy.md").read_text(encoding="utf-8")
    assert "web.lock" in text
    assert "aldwin_session" in text
