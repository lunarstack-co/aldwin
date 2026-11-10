"""Session token, cookie and Origin/Host checks for the loopback web door."""

from __future__ import annotations

import ipaddress
import secrets
from dataclasses import dataclass, field
from typing import Literal, TypedDict

COOKIE_NAME = "aldwin_session"
TOKEN_QUERY = "t"


class CookieSettings(TypedDict):
    """Keyword arguments for Starlette's ``Response.set_cookie``."""

    key: str
    value: str
    httponly: bool
    samesite: Literal["strict"]
    path: str
    secure: bool


def is_loopback_host(host: str) -> bool:
    """Return whether ``host`` names this computer only."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def bind_address(host: str) -> str:
    """Return the IP address to bind for a loopback ``host``."""
    return "127.0.0.1" if host == "localhost" else host


def _url_host(host: str) -> str:
    address = bind_address(host)
    return f"[{address}]" if ":" in address else address


def origin_for(host: str, port: int) -> str:
    """Return ``http://host:port`` for a loopback ``host``, bracketing IPv6."""
    return f"http://{_url_host(host)}:{port}"


@dataclass(frozen=True)
class Authenticator:
    """Holds this run's session token and the origins allowed to use it."""

    token: str
    host: str
    port: int
    _hosts: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        name = _url_host(self.host)
        object.__setattr__(
            self,
            "_hosts",
            frozenset({f"{name}:{self.port}", f"localhost:{self.port}"}),
        )

    @classmethod
    def mint(cls, host: str, port: int) -> Authenticator:
        """Create an authenticator with a fresh random token."""
        return cls(token=secrets.token_urlsafe(32), host=host, port=port)

    @property
    def origin(self) -> str:
        """Return the origin the browser is sent to, without a trailing slash."""
        return origin_for(self.host, self.port)

    @property
    def launch_url(self) -> str:
        """Return the one-time URL that sets the session cookie."""
        return f"{self.origin}/?{TOKEN_QUERY}={self.token}"

    def host_ok(self, host_header: str | None) -> bool:
        """Return whether the ``Host`` header names the bound address and port.

        This is the DNS-rebinding guard: a hostile page that re-points its own
        name at 127.0.0.1 still sends its own name here.
        """
        return host_header is not None and host_header.lower() in self._hosts

    def origin_ok(self, origin: str | None) -> bool:
        """Return whether ``Origin`` is absent or one of the bound origins."""
        if origin is None:
            return True
        scheme, sep, rest = origin.lower().partition("://")
        return scheme == "http" and sep == "://" and rest in self._hosts

    def fetch_site_ok(self, fetch_site: str | None) -> bool:
        """Return whether ``Sec-Fetch-Site`` is absent, same-origin or typed in.

        Cookies ignore ports, so a page on another loopback port gets the cookie
        and may send no ``Origin``; the browser still labels it ``same-site``.
        """
        return fetch_site is None or fetch_site.lower() in ("same-origin", "none")

    def token_ok(self, value: str | None) -> bool:
        """Return whether ``value`` is this run's token, in constant time."""
        if value is None:
            return False
        return secrets.compare_digest(value.encode("utf-8"), self.token.encode("utf-8"))

    def cookie_kwargs(self) -> CookieSettings:
        """Return the ``set_cookie`` arguments for the session cookie.

        ``Secure`` stays off because the door is plain http on loopback, and a
        Secure cookie would never be sent back.
        """
        return {
            "key": COOKIE_NAME,
            "value": self.token,
            "httponly": True,
            "samesite": "strict",
            "path": "/",
            "secure": False,
        }
