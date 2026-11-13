"""Session token, cookie settings and Origin/Host checks for the web door."""

from __future__ import annotations

import pytest

from aldwin.web.auth import (
    COOKIE_NAME,
    Authenticator,
    bind_address,
    is_loopback_host,
    origin_for,
)


def test_cookie_kwargs_are_strict_http_only_and_not_secure() -> None:
    """HttpOnly, SameSite=Strict, Path=/, and no Secure on http."""
    auth = Authenticator.mint("127.0.0.1", 7841)
    assert auth.cookie_kwargs() == {
        "key": COOKIE_NAME,
        "value": auth.token,
        "httponly": True,
        "samesite": "strict",
        "path": "/",
        "secure": False,
    }
    assert COOKIE_NAME == "aldwin_session"


def test_mint_makes_a_fresh_unguessable_token() -> None:
    """Each run gets its own token, long enough not to be guessed."""
    first = Authenticator.mint("127.0.0.1", 7841)
    second = Authenticator.mint("127.0.0.1", 7841)
    assert first.token != second.token
    assert len(first.token) >= 40


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("127.0.0.1", "http://127.0.0.1:7841"),
        ("localhost", "http://127.0.0.1:7841"),
        ("::1", "http://[::1]:7841"),
    ],
)
def test_origin_and_launch_url(host: str, origin: str) -> None:
    """The origin brackets IPv6 and the launch URL carries the token."""
    auth = Authenticator.mint(host, 7841)
    assert auth.origin == origin == origin_for(host, 7841)
    assert auth.launch_url == f"{origin}/?t={auth.token}"


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost", "::1"])
def test_loopback_hosts_are_accepted(host: str) -> None:
    """Loopback names and addresses are allowed."""
    assert is_loopback_host(host)


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "::", "8.8.8.8", "192.168.1.10", "example.com", ""]
)
def test_other_hosts_are_refused(host: str) -> None:
    """Anything reachable from another computer is refused."""
    assert not is_loopback_host(host)


def test_localhost_binds_ipv4_loopback() -> None:
    assert bind_address("localhost") == "127.0.0.1"
    assert bind_address("::1") == "::1"


@pytest.mark.parametrize(
    ("header", "ok"),
    [
        ("127.0.0.1:7841", True),
        ("localhost:7841", True),
        ("LOCALHOST:7841", True),
        ("127.0.0.1:7842", False),
        ("127.0.0.1", False),
        ("evil.example:7841", False),
        (None, False),
    ],
)
def test_host_header_must_name_the_bound_address(header: str | None, ok: bool) -> None:
    """A rebound hostname or another port is refused."""
    assert Authenticator.mint("127.0.0.1", 7841).host_ok(header) is ok


def test_ipv6_host_header_is_bracketed() -> None:
    auth = Authenticator.mint("::1", 7841)
    assert auth.host_ok("[::1]:7841")
    assert not auth.host_ok("::1:7841")


@pytest.mark.parametrize(
    ("origin", "ok"),
    [
        (None, True),
        ("http://127.0.0.1:7841", True),
        ("http://localhost:7841", True),
        ("https://127.0.0.1:7841", False),
        ("http://evil.example:7841", False),
        ("http://127.0.0.1:7841.evil.example", False),
        ("null", False),
    ],
)
def test_origin_must_be_absent_or_bound(origin: str | None, ok: bool) -> None:
    """A missing Origin passes; a foreign one does not."""
    assert Authenticator.mint("127.0.0.1", 7841).origin_ok(origin) is ok


@pytest.mark.parametrize(
    ("fetch_site", "ok"),
    [
        (None, True),
        ("same-origin", True),
        ("none", True),
        ("SAME-ORIGIN", True),
        ("same-site", False),
        ("cross-site", False),
    ],
)
def test_fetch_site_must_be_same_origin_or_typed(
    fetch_site: str | None, ok: bool
) -> None:
    """A page on another loopback port is same-site, not same-origin."""
    assert Authenticator.mint("127.0.0.1", 7841).fetch_site_ok(fetch_site) is ok


def test_token_check() -> None:
    """Only this run's exact token passes."""
    auth = Authenticator.mint("127.0.0.1", 7841)
    assert auth.token_ok(auth.token)
    assert not auth.token_ok(None)
    assert not auth.token_ok("")
    assert not auth.token_ok(auth.token + "x")
