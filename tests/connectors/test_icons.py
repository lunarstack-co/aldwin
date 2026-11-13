"""Finding, keeping and reading a connected app's own icon."""

from __future__ import annotations

import base64
from pathlib import Path

import httpx2
import pytest

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.icons import (
    MAX_ICON_BYTES,
    fetch_icon,
    icon_path,
    icon_version,
    load_icon,
    remove_icon,
    same_site,
    save_icon,
)

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><path d="M0 0z"/></svg>'
)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
SERVER = ConnectorConfig(name="mail", url="https://mcp.example.com/api/mcp")


def _site(routes: dict[str, httpx2.Response]) -> tuple[httpx2.AsyncClient, list[str]]:
    asked: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        asked.append(str(request.url))
        assert "authorization" not in request.headers
        return routes.get(str(request.url), httpx2.Response(404))

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), asked


def _image(data: bytes, mime: str) -> httpx2.Response:
    return httpx2.Response(200, content=data, headers={"content-type": mime})


async def test_a_declared_data_icon_is_used_without_any_request() -> None:
    http, asked = _site({})
    src = "data:image/png;base64," + base64.b64encode(PNG).decode()
    async with http:
        assert await fetch_icon(SERVER, [(src, None)], http=http) == (PNG, "image/png")
    assert asked == []


async def test_a_declared_icon_on_the_servers_site_is_fetched_first() -> None:
    url = "https://example.com/brand/icon.png"
    http, asked = _site({url: _image(PNG, "image/png")})
    async with http:
        assert await fetch_icon(SERVER, [(url, "image/png")], http=http) == (
            PNG,
            "image/png",
        )
    assert asked == [url]


async def test_a_declared_icon_on_another_site_is_never_fetched() -> None:
    http, asked = _site({})
    async with http:
        found = await fetch_icon(
            SERVER, [("https://tracker.example.net/pixel.png", None)], http=http
        )
    assert found is None
    assert all("example.net" not in url for url in asked)


async def test_without_a_declared_icon_the_sites_usual_addresses_are_tried() -> None:
    http, asked = _site(
        {"https://example.com/favicon.svg": _image(SVG, "image/svg+xml; charset=utf-8")}
    )
    async with http:
        assert await fetch_icon(SERVER, http=http) == (SVG, "image/svg+xml")
    assert asked == [
        "https://mcp.example.com/favicon.svg",
        "https://mcp.example.com/apple-touch-icon.png",
        "https://mcp.example.com/favicon.ico",
        "https://example.com/favicon.svg",
    ]


@pytest.mark.parametrize(
    "response",
    [
        _image(b"<html>not found</html>", "text/html"),
        _image(b"x" * (MAX_ICON_BYTES + 1), "image/png"),
        _image(b"", "image/png"),
        httpx2.Response(
            302, headers={"location": "https://elsewhere.example.net/i.png"}
        ),
        httpx2.Response(500),
    ],
)
async def test_a_page_an_oversized_file_or_a_redirect_elsewhere_is_no_icon(
    response: httpx2.Response,
) -> None:
    http, asked = _site({"https://mcp.example.com/favicon.svg": response})
    async with http:
        assert await fetch_icon(SERVER, http=http) is None
    assert all("example.net" not in url for url in asked)


async def test_a_redirect_within_the_site_is_followed() -> None:
    http, _asked = _site(
        {
            "https://mcp.example.com/favicon.svg": httpx2.Response(
                301, headers={"location": "https://www.example.com/icon.svg"}
            ),
            "https://www.example.com/icon.svg": _image(SVG, "image/svg+xml"),
        }
    )
    async with http:
        assert await fetch_icon(SERVER, http=http) == (SVG, "image/svg+xml")


async def test_a_local_command_has_no_site_to_ask() -> None:
    http, asked = _site({})
    async with http:
        assert (
            await fetch_icon(ConnectorConfig(name="tool", command="npx"), http=http)
            is None
        )
    assert asked == []


def test_same_site_needs_https_and_the_servers_own_domain() -> None:
    server = "https://mcp.example.com/mcp"
    assert same_site("https://example.com/i.png", server)
    assert not same_site("http://example.com/i.png", server)
    assert not same_site("https://example.org/i.png", server)
    assert same_site("http://127.0.0.1:9000/favicon.ico", "http://127.0.0.1:9000/mcp")
    assert not same_site("http://127.0.0.2/favicon.ico", "http://127.0.0.1:9000/mcp")


def test_only_the_server_host_and_its_parent_count_never_a_whole_suffix() -> None:
    assert same_site("https://example.co.uk/i.png", "https://mcp.example.co.uk/mcp")
    assert same_site("https://www.example.co.uk/i.png", "https://mcp.example.co.uk/mcp")
    assert not same_site("https://evil.co.uk/i.png", "https://mcp.example.co.uk/mcp")
    assert not same_site("https://other.github.io/i.png", "https://you.github.io/mcp")
    assert same_site("https://www.mail.com/i.png", "https://mail.com/api/mcp")
    assert not same_site("https://evil.com/i.png", "https://mail.com/api/mcp")


def test_an_icon_is_kept_replaced_and_removed(aldwin_home: Path) -> None:
    assert load_icon(aldwin_home, "mail") is None
    save_icon(aldwin_home, "mail", (PNG, "image/png"))
    first = icon_version(aldwin_home, "mail")
    assert load_icon(aldwin_home, "mail") == (PNG, "image/png")
    path = icon_path(aldwin_home, "mail")
    assert path is not None and path.stat().st_mode & 0o777 == 0o600

    save_icon(aldwin_home, "mail", (SVG, "image/svg+xml"))
    assert load_icon(aldwin_home, "mail") == (SVG, "image/svg+xml")
    assert first is not None and len(list(path.parent.iterdir())) == 1

    remove_icon(aldwin_home, "mail")
    assert icon_version(aldwin_home, "mail") is None


@pytest.mark.parametrize("name", ["../secrets", "a/b", "", "UPPER", "x" * 40])
def test_a_name_that_is_not_a_connector_name_has_no_icon(
    aldwin_home: Path, name: str
) -> None:
    assert load_icon(aldwin_home, name) is None
    assert icon_path(aldwin_home, name) is None
