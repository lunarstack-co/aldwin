"""One-time WebSocket tickets and ``POST /v1/ws-ticket``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aldwin.web.app import create_app
from aldwin.web.auth import COOKIE_NAME, Authenticator
from aldwin.web.routes.ws import HOST_NOT_READY
from aldwin.web.runtime import WebHostRuntime
from aldwin.web.tickets import DEFAULT_TICKET_TTL, TicketStore

PORT = 7841
BASE = f"http://127.0.0.1:{PORT}"


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_tickets_are_unique_and_spent_once() -> None:
    """TC-10, TC-11: each ticket is new and opens exactly one socket."""
    store = TicketStore()
    first, second = store.mint(), store.mint()
    assert first != second and len(first) >= 40
    assert store.consume(first)
    assert not store.consume(first)
    assert store.consume(second)
    assert not store.consume("never-minted")
    assert not store.consume("")
    assert not store.consume(None)


def test_ticket_expires_after_its_lifetime() -> None:
    """TC-12: a ticket older than its lifetime is refused, and spent anyway."""
    clock = Clock()
    store = TicketStore(ttl=30.0, clock=clock)
    kept, late = store.mint(), store.mint()
    clock.now += 29.9
    assert store.consume(kept)
    clock.now += 0.2
    assert not store.consume(late)
    assert not store.consume(late)


def test_expired_tickets_are_dropped_when_new_ones_are_minted() -> None:
    clock = Clock()
    store = TicketStore(ttl=1.0, clock=clock)
    for _ in range(5):
        store.mint()
    clock.now += 2
    store.mint()
    assert len(store._expires) == 1


def test_minting_prunes_in_place() -> None:
    """Spending and pruning work on one dict, so a spent ticket stays spent."""
    clock = Clock()
    store = TicketStore(ttl=1.0, clock=clock)
    spent = store.mint()
    table = store._expires
    assert store.consume(spent)
    clock.now += 2
    store.mint()
    assert store._expires is table
    assert not store.consume(spent)


def test_ticket_route_runs_on_the_event_loop(tmp_path: Path) -> None:
    """A threaded mint could race a spend on the loop; the route must be async."""
    import inspect

    from fastapi.routing import APIRoute

    from aldwin.web.routes.ws import ws_router

    router = ws_router(
        WebHostRuntime(home=tmp_path), Authenticator.mint("127.0.0.1", PORT)
    )
    mint = next(r for r in router.routes if isinstance(r, APIRoute))
    assert inspect.iscoroutinefunction(mint.endpoint)


def test_default_lifetime_is_thirty_seconds() -> None:
    assert DEFAULT_TICKET_TTL == 30.0
    assert TicketStore().ttl == 30.0


@pytest.fixture
def runtime(aldwin_home: Path) -> WebHostRuntime:
    return WebHostRuntime(home=aldwin_home)


@pytest.fixture
def auth() -> Authenticator:
    return Authenticator.mint("127.0.0.1", PORT)


@pytest.fixture
def client(
    runtime: WebHostRuntime, auth: Authenticator, aldwin_home: Path, tmp_path: Path
) -> Iterator[TestClient]:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<main>Aldwin</main>", encoding="utf-8")
    app = create_app(
        authenticator=auth,
        home=aldwin_home,
        home_display="~",
        version="1",
        static_dir=static,
        runtime=runtime,
    )
    with TestClient(app, base_url=BASE) as test_client:
        yield test_client


def _sign_in(client: TestClient, auth: Authenticator) -> None:
    assert client.get(f"/?t={auth.token}").status_code == 200


def test_ticket_is_minted_for_the_signed_in_page(
    client: TestClient, auth: Authenticator, runtime: WebHostRuntime
) -> None:
    """TC-06: a same-origin request with the cookie gets a 30-second ticket."""
    runtime.mark_ready()
    _sign_in(client, auth)

    response = client.post("/v1/ws-ticket", headers={"Origin": BASE})

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == 30
    assert runtime.tickets.consume(body["ticket"])


def test_ticket_needs_the_session_cookie(
    client: TestClient, runtime: WebHostRuntime
) -> None:
    """TC-07: no cookie means 401 and no ticket."""
    runtime.mark_ready()
    response = client.post("/v1/ws-ticket", headers={"Origin": BASE})
    assert response.status_code == 401
    assert "ticket" not in response.json()


def test_ticket_with_a_wrong_cookie_is_401(
    client: TestClient, runtime: WebHostRuntime
) -> None:
    """TC-23: a cookie from another run does not mint a ticket."""
    runtime.mark_ready()
    client.cookies.set(COOKIE_NAME, "not-this-run")
    assert client.post("/v1/ws-ticket").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "http://127.0.0.1:3000"},
        {"Host": "evil.example:7841"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_ticket_from_another_page_is_403(
    client: TestClient,
    auth: Authenticator,
    runtime: WebHostRuntime,
    headers: dict[str, str],
) -> None:
    """TC-08: the cookie alone is not enough from another site or port."""
    runtime.mark_ready()
    _sign_in(client, auth)
    response = client.post("/v1/ws-ticket", headers=headers)
    assert response.status_code == 403
    assert "ticket" not in response.json()


def test_ticket_waits_for_a_host(client: TestClient, auth: Authenticator) -> None:
    """TC-09: before a host is serving, minting answers 503 host_not_ready."""
    _sign_in(client, auth)
    response = client.post("/v1/ws-ticket", headers={"Origin": BASE})
    assert response.status_code == 503
    assert response.json()["error"] == HOST_NOT_READY


async def test_shutdown_stops_minting(runtime: WebHostRuntime) -> None:
    runtime.mark_ready()
    assert runtime.ready
    await runtime.shutdown()
    assert not runtime.ready
    assert runtime.transport.closed
