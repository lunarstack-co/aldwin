"""A connected app's own icon: found once, kept in the home folder, shown by doors.

The icon comes from what the server declares about itself, or else from the
usual icon addresses on the server's own site. Nothing is fetched from any
other site, and no sign-in is sent with the request.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Final
from urllib.parse import unquote_to_bytes, urljoin, urlsplit

import httpx2

from aldwin.core.connectors.config import NAME_PATTERN, ConnectorConfig
from aldwin.core.home import DIR_MODE, atomic_write

logger = logging.getLogger(__name__)

MAX_ICON_BYTES: Final = 256 * 1024
FETCH_TIMEOUT_S: Final = 8.0
MAX_REDIRECTS: Final = 3
ICONS_SUBDIR: Final = "state/connector-icons"
# Tried in order on the server's own site when it declares no icon.
FALLBACK_PATHS: Final = ("/favicon.svg", "/apple-touch-icon.png", "/favicon.ico")

_EXTENSIONS: Final[dict[str, str]] = {
    "image/svg+xml": "svg",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/x-icon": "ico",
}
_ALIASES: Final[dict[str, str]] = {
    "image/vnd.microsoft.icon": "image/x-icon",
    "image/jpg": "image/jpeg",
}
_MEDIA_TYPES: Final[dict[str, str]] = {ext: mime for mime, ext in _EXTENSIONS.items()}
_NAME_RE = re.compile(NAME_PATTERN)

Icon = tuple[bytes, str]


def _media_type(raw: str | None) -> str | None:
    if not raw:
        return None
    mime = raw.split(";", 1)[0].strip().lower()
    mime = _ALIASES.get(mime, mime)
    return mime if mime in _EXTENSIONS else None


def _parent(host: str) -> str | None:
    """`example.com` for `mcp.example.com`; none for an IP address or a two-label host.

    Only the direct parent is trusted, never every host under a suffix, so
    `you.github.io` does not open up the rest of `github.io`.
    """
    labels = host.split(".")
    if host.replace(".", "").isdigit() or ":" in host or len(labels) < 3:
        return None
    return ".".join(labels[1:])


def allowed_hosts(server_host: str) -> set[str]:
    """The server's host, its parent, and the `www.` form sites often redirect to."""
    root = _parent(server_host) or server_host
    hosts = {server_host, root}
    if not root.replace(".", "").isdigit() and ":" not in root:
        hosts.add(f"www.{root}")
    return hosts


def same_site(url: str, server_url: str) -> bool:
    """Whether `url` is an https (or same-scheme) address on the server's own site."""
    target, server = urlsplit(url), urlsplit(server_url)
    if target.scheme not in ("https", server.scheme) or not target.hostname:
        return False
    return target.hostname in allowed_hosts(server.hostname or "")


def _from_data_uri(src: str, declared: str | None) -> Icon | None:
    header, _, payload = src[5:].partition(",")
    mime = _media_type(header) or _media_type(declared)
    if mime is None or not payload:
        return None
    try:
        data = (
            base64.b64decode(payload, validate=True)
            if header.endswith(";base64")
            else unquote_to_bytes(payload)
        )
    except (binascii.Error, ValueError):
        return None
    return (data, mime) if 0 < len(data) <= MAX_ICON_BYTES else None


async def _get(http: httpx2.AsyncClient, url: str, server_url: str) -> Icon | None:
    """One image from the server's site; a redirect is followed only within it."""
    for _ in range(MAX_REDIRECTS + 1):
        if not same_site(url, server_url):
            return None
        async with http.stream("GET", url) as response:
            if response.is_redirect and "location" in response.headers:
                url = urljoin(url, response.headers["location"])
                continue
            mime = _media_type(response.headers.get("content-type"))
            if response.status_code != 200 or mime is None:
                return None
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data += chunk
                if len(data) > MAX_ICON_BYTES:
                    return None
            return (bytes(data), mime) if data else None
    return None


def _fallback_urls(server_url: str) -> list[str]:
    parts = urlsplit(server_url)
    hosts = [parts.netloc]
    root = _parent(parts.hostname or "")
    if root is not None and not parts.port:
        hosts.append(root)
    return [
        f"{parts.scheme}://{host}{path}" for host in hosts for path in FALLBACK_PATHS
    ]


async def fetch_icon(
    config: ConnectorConfig,
    declared: Sequence[tuple[str, str | None]] = (),
    *,
    http: httpx2.AsyncClient | None = None,
) -> Icon | None:
    """The app's icon as `(bytes, media type)`, or `None` when it has none.

    `declared` is what the server said about itself: `(src, media type)` pairs.
    A local command has no site to ask, so only a declared `data:` icon counts.
    """
    for src, mime in declared:
        if src.startswith("data:"):
            found = _from_data_uri(src, mime)
            if found is not None:
                return found
    if config.url is None:
        return None

    from aldwin.core.tools import USER_AGENT

    urls = [src for src, _mime in declared if not src.startswith("data:")]
    urls += _fallback_urls(config.url)
    owned = http is None
    client = http or httpx2.AsyncClient(
        timeout=FETCH_TIMEOUT_S, headers={"user-agent": USER_AGENT}
    )
    try:
        for url in urls:
            try:
                found = await _get(client, url, config.url)
            except httpx2.HTTPError as exc:
                logger.debug("icon %s for %s: %s", url, config.name, exc)
                continue
            if found is not None:
                return found
    finally:
        if owned:
            await client.aclose()
    return None


def _files(home: Path, name: str) -> list[Path]:
    if not _NAME_RE.fullmatch(name):
        return []
    folder = home / ICONS_SUBDIR
    return [folder / f"{name}.{ext}" for ext in _MEDIA_TYPES]


def save_icon(home: Path, name: str, icon: Icon) -> None:
    """Keep `icon` for the connector, replacing any earlier one."""
    data, mime = icon
    remove_icon(home, name)
    folder = home / ICONS_SUBDIR
    folder.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    atomic_write(folder / f"{name}.{_EXTENSIONS[mime]}", data)


def remove_icon(home: Path, name: str) -> None:
    for path in _files(home, name):
        path.unlink(missing_ok=True)


def icon_path(home: Path, name: str) -> Path | None:
    """The saved icon file, or `None`; a name that is not a connector name has none."""
    return next((path for path in _files(home, name) if path.is_file()), None)


def icon_version(home: Path, name: str) -> str | None:
    """Changes whenever the saved icon does, so a door knows to load it again."""
    path = icon_path(home, name)
    return None if path is None else f"{path.stat().st_mtime_ns:x}"


def load_icon(home: Path, name: str) -> Icon | None:
    path = icon_path(home, name)
    if path is None:
        return None
    try:
        return path.read_bytes(), _MEDIA_TYPES[path.suffix[1:]]
    except OSError:
        return None
