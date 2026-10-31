"""The loopback listener and pasted-address parsing."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator

import httpx2
import pytest

from aldwin.core.connectors.errors import SignInError
from aldwin.core.connectors.oauth_callback import LoopbackCallback, parse_redirect_url
from aldwin.core.connectors.oauth_messages import CALLBACK_PORTS

pytestmark = pytest.mark.loopback


def _free(port: int) -> bool:
    with socket.socket() as probe:
        # Same option asyncio's servers use, so TIME_WAIT leftovers don't count.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@pytest.fixture(autouse=True)
def ports_free() -> None:
    if not all(_free(p) for p in CALLBACK_PORTS):
        pytest.skip("ports 47823-47825 are in use on this machine")


@pytest.fixture
def held() -> Iterator[list[socket.socket]]:
    sockets: list[socket.socket] = []
    yield sockets
    for sock in sockets:
        sock.close()


def _hold(held: list[socket.socket], port: int) -> None:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen()
    held.append(sock)


async def test_binds_the_first_port_on_loopback_only() -> None:
    callback = await LoopbackCallback.bind()
    try:
        assert callback.port == 47823
        assert callback._server.sockets[0].getsockname()[0] == "127.0.0.1"
    finally:
        await callback.close()


async def test_busy_port_falls_back(held: list[socket.socket]) -> None:
    """`TC-04` (unit half)."""
    _hold(held, 47823)
    callback = await LoopbackCallback.bind()
    assert callback.port == 47824
    await callback.close()


async def test_all_ports_busy(held: list[socket.socket]) -> None:
    for port in CALLBACK_PORTS:
        _hold(held, port)
    with pytest.raises(SignInError) as info:
        await LoopbackCallback.bind()
    assert info.value.message == (
        "Ports 47823-47825 are busy; close the app using them or use the paste option."
    )


async def test_wrong_state_rejected_then_right_state_accepted() -> None:
    """`TC-05`."""
    callback = await LoopbackCallback.bind()
    callback.expect_state("good")
    base = f"http://127.0.0.1:{callback.port}/callback"
    try:
        async with httpx2.AsyncClient() as http:
            wrong = await http.get(base, params={"code": "c", "state": "bad"})
            assert wrong.status_code == 400
            assert "expired" in wrong.text
            right = await http.get(
                base, params={"code": "c1", "state": "good", "iss": "i"}
            )
            assert right.status_code == 200
            assert "You're signed in" in right.text
        result = await asyncio.wait_for(callback.result(), 2)
        assert (result.code, result.state, result.iss) == ("c1", "good", "i")
    finally:
        await callback.close()


async def test_declined_in_the_browser() -> None:
    callback = await LoopbackCallback.bind()
    callback.expect_state("s")
    try:
        async with httpx2.AsyncClient() as http:
            page = await http.get(
                f"http://127.0.0.1:{callback.port}/callback",
                params={"error": "access_denied", "state": "s"},
            )
        assert "declined" in page.text
        result = await asyncio.wait_for(callback.result(), 2)
        assert result.error == "access_denied" and result.code is None
    finally:
        await callback.close()


async def test_other_paths_are_404() -> None:
    callback = await LoopbackCallback.bind()
    try:
        async with httpx2.AsyncClient() as http:
            assert (
                await http.get(f"http://127.0.0.1:{callback.port}/x")
            ).status_code == 404
    finally:
        await callback.close()


def test_parse_redirect_url() -> None:
    url = "http://127.0.0.1:47823/callback?code=abc&state=s1&iss=https%3A%2F%2Fa"
    result = parse_redirect_url(f"  {url}  ", expected_state="s1")
    assert (result.code, result.iss) == ("abc", "https://a")
    with pytest.raises(SignInError) as other:
        parse_redirect_url(url, expected_state="s2")
    assert other.value.message == (
        "That address is from a different sign-in. Paste the address from this sign-in."
    )
    with pytest.raises(SignInError) as bare:
        parse_redirect_url("http://127.0.0.1:47823/callback", expected_state="s1")
    assert bare.value.message == (
        "That address has no sign-in code. "
        "Paste the full address your browser ended on."
    )
