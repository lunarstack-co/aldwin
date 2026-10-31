"""Shared pytest fixtures and helpers for Aldwin."""

from __future__ import annotations

import hashlib
import random
import socket
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any, Literal

import pytest
from agentling import AgentSession
from canary import CANARY
from fakes.host import FakeHost
from fakes.model import FakeModel

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent, new_session
from aldwin.core.home import ensure_home
from aldwin.core.policy import REGISTRY, ApprovalAnswer, ApprovalRequest
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.core.workspace import Workspace
from aldwin.host import AldwinHost, AskFn, InProcessTransport
from aldwin.protocol import ApprovalAnswer as ProtocolApprovalAnswer
from aldwin.protocol import ApprovalEvent, Event

_ORIG_SOCKET_CONNECT = socket.socket.connect
_ORIG_SOCKET_CONNECT_EX = socket.socket.connect_ex
_ORIG_CREATE_CONNECTION = socket.create_connection
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Snapshot of the real user home before any test redirects HOME.
_REAL_USER_HOME = Path.home().resolve()
_REAL_ALDWIN = _REAL_USER_HOME / ".aldwin"


async def _allow_ask(_summary: str, _detail: str = "") -> bool:
    return True


async def collect(aiter: AsyncIterator[Event]) -> list[Event]:
    """Drain an async iterator of protocol `Event`s into a list."""
    events: list[Event] = []
    async for ev in aiter:
        events.append(ev)
    return events


async def auto_approve(
    client: AldwinClient,
    aiter: AsyncIterator[Event],
    answer: ProtocolApprovalAnswer = "yes",
) -> list[Event]:
    """Consume a turn stream and automatically answer every `ApprovalEvent`."""
    events: list[Event] = []
    async for ev in aiter:
        events.append(ev)
        if isinstance(ev, ApprovalEvent):
            await client.approve(ev.approval_id, answer, digest=ev.digest)
    return events


_NET_ERR = "Network access is disabled in default tests; mark with @pytest.mark.live"


def _allows_network(request: pytest.FixtureRequest) -> bool:
    return "live" in request.keywords or "e2e" in request.keywords


def _fingerprint_tree(root: Path) -> dict[str, str]:
    """Map relative paths under `root` to content hashes (files only)."""
    if not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        out[rel] = digest
    return out


@pytest.fixture(scope="session")
def assert_real_home_unchanged() -> Iterator[None]:
    """Fail the session if tests mutate the real `~/.aldwin`."""
    before = _fingerprint_tree(_REAL_ALDWIN)
    yield
    after = _fingerprint_tree(_REAL_ALDWIN)
    assert after == before, (
        "Real ~/.aldwin changed during the test session. "
        f"before={sorted(before)} after={sorted(after)}"
    )


@pytest.fixture(autouse=True)
def _session_home_guard(assert_real_home_unchanged: None) -> None:
    """Pull the session home-guard into every test collection."""
    return None


@pytest.fixture(autouse=True)
def aldwin_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Path:
    """Redirect `ALDWIN_HOME` and `HOME` to `tmp_path` and run `ensure_home()`."""
    user_home = tmp_path / "user"
    user_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(user_home))
    # Some sandboxes export TERM=dumb, NO_COLOR, and FORCE_COLOR=0.
    # Rich then ignores an explicit width, drops styles, and treats the
    # console as non-interactive. Tests that need plain mode set NO_COLOR
    # themselves.
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    home = tmp_path / "home"
    monkeypatch.setenv("ALDWIN_HOME", str(home))

    if not _allows_network(request):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    if "uninitialized_home" not in request.keywords:
        ensure_home(home)

    return home


@pytest.fixture(autouse=True)
def no_network(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Block outbound `AF_INET`/`AF_INET6` sockets unless marked `live`/`e2e`.

    Tests marked `loopback` may still reach `127.0.0.1` and `::1` (fixture servers).
    """
    if _allows_network(request):
        return
    loopback_ok = "loopback" in request.keywords

    def _allowed(address: Any) -> bool:
        return (
            loopback_ok
            and isinstance(address, tuple)
            and (address[0] in _LOOPBACK_HOSTS)
        )

    def _blocked_connect(self: socket.socket, address: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _allowed(address):
            raise RuntimeError(_NET_ERR)
        return _ORIG_SOCKET_CONNECT(self, address)

    def _blocked_connect_ex(self: socket.socket, address: Any) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _allowed(address):
            raise RuntimeError(_NET_ERR)
        return _ORIG_SOCKET_CONNECT_EX(self, address)

    def _blocked_create_connection(
        address: Any, *args: Any, **kwargs: Any
    ) -> socket.socket:
        if not _allowed(address):
            raise RuntimeError(_NET_ERR)
        return _ORIG_CREATE_CONNECTION(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect_ex)
    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection)


@pytest.fixture(autouse=True)
def no_runtime_policy_leak() -> Iterator[None]:
    """Fail a test that leaves a connector (`mcp__`) entry in the policy `REGISTRY`."""
    yield
    leaked = sorted(name for name in REGISTRY if name.startswith("mcp__"))
    for name in leaked:
        del REGISTRY[name]
    assert leaked == [], f"runtime policy entries leaked: {leaked}"


@pytest.fixture
def workspace(tmp_path: Path, aldwin_home: Path) -> Workspace:
    """Isolated workspace directory (`tmp_path/ws`) for file and security tests."""
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    return Workspace(root, home=aldwin_home)


@pytest.fixture
def fixture_server() -> Any:
    """A fresh fixture MCP server (`FixtureControls`), for in-process use."""
    from fixtures.mcp_server import build_fixture_server

    return build_fixture_server()


@pytest.fixture
async def fixture_url(fixture_server: Any) -> AsyncIterator[str]:
    """`fixture_server` over HTTP on loopback; the base URL (MCP is at `/mcp`).

    Tests using it need `@pytest.mark.loopback`.
    """
    from fixtures.mcp_server import serve_http

    async with serve_http(fixture_server) as url:
        yield url


@pytest.fixture
def canary_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Put the canary value in the process environment; return the constant."""
    monkeypatch.setenv("ALDWIN_CANARY", CANARY)
    monkeypatch.setenv(CANARY, "1")
    return CANARY


@pytest.fixture
def seeded_rng(monkeypatch: pytest.MonkeyPatch) -> random.Random:
    """Deterministic RNG for tests that need reproducible randomness."""
    rng = random.Random(433)
    monkeypatch.setattr(random, "random", rng.random)
    monkeypatch.setattr(random, "choice", rng.choice)
    monkeypatch.setattr(random, "randrange", rng.randrange)
    return rng


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[float], None]]:
    """Injectable clock: call `set_now(ts)` then `time.time()` returns `ts`."""
    state = {"now": 1_724_000_000.0}

    def set_now(ts: float) -> None:
        state["now"] = ts

    monkeypatch.setattr(time, "time", lambda: state["now"])
    yield set_now


@pytest.fixture
def approver() -> Callable[..., Any]:
    """Build an ask callback that returns a fixed answer, echoing digests."""

    def _factory(
        answer: Literal["yes", "yes_session", "no"] = "yes",
        reason: str | None = None,
        *,
        asks: list[ApprovalRequest] | None = None,
    ) -> Callable[[ApprovalRequest], Any]:
        bucket = asks if asks is not None else []

        async def ask(req: ApprovalRequest) -> ApprovalAnswer:
            bucket.append(req)
            return ApprovalAnswer(answer, "user", reason=reason)

        return ask

    return _factory


@pytest.fixture
def fake_model() -> FakeModel:
    """Return a fresh, network-free `FakeModel` instance."""
    return FakeModel()


@pytest.fixture
def agent_session(fake_model: FakeModel, aldwin_home: Path) -> AgentSession:
    """Build a real `aldwin.core` `AgentSession` wired to `fake_model`."""
    agent = build_agent(
        AldwinConfig(),
        SecretsConfig(),
        _allow_ask,
        home=aldwin_home,
        model=fake_model,
    )
    return new_session(agent)


@pytest.fixture
async def host_client(
    fake_model: FakeModel,
    aldwin_home: Path,
) -> AsyncIterator[AldwinClient]:
    """Start an in-process `AldwinHost` on `fake_model` and yield a connected client."""

    def builder(model_name: str, ask: AskFn) -> Any:
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        )

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder)
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        yield client
    finally:
        await client.close()
        await host.stop_host()


@pytest.fixture
def fake_host() -> FakeHost:
    """Return a scriptable `FakeHost` test double for CLI tests."""
    return FakeHost()
